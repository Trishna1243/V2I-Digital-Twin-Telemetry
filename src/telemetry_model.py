"""Deterministic, shared-channel telemetry access and metric aggregation.

Each RSU owns one independent 10 MHz channel. A perfectly coordinated,
non-preemptive FCFS scheduler serializes packets on that RSU; simultaneous
arrivals are ordered by vehicle ID. A packet is delivered only when its
capacity-limited serialization completes by its next-upload deadline and
within the observation window. This intentionally simple model has no
collisions: queue deadline misses are its only packet-loss mechanism.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
from numbers import Integral, Real
from typing import Iterable, Mapping


@dataclass(frozen=True)
class PacketAttempt:
    packet_id: str
    vehicle_id: str
    rsu_id: str
    generated_at_seconds: float
    deadline_seconds: float
    serialization_seconds: float

    def __post_init__(self) -> None:
        if not self.packet_id or not self.vehicle_id or not self.rsu_id:
            raise ValueError("packet, vehicle, and RSU IDs must be nonempty")
        for name in ("generated_at_seconds", "deadline_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.deadline_seconds <= self.generated_at_seconds:
            raise ValueError("deadline must be after packet generation")
        if not math.isfinite(self.serialization_seconds) or self.serialization_seconds <= 0:
            raise ValueError("serialization_seconds must be finite and positive")


@dataclass(frozen=True)
class PacketOutcome:
    attempt: PacketAttempt
    delivered: bool
    queued_at_seconds: float
    completed_at_seconds: float | None
    loss_reason: str | None


def schedule_shared_channel(
    attempts: Iterable[PacketAttempt], observation_end_seconds: float
) -> list[PacketOutcome]:
    """Queue transmissions independently per RSU using stable FCFS ordering.

    Arrivals with equal timestamps use lexicographic vehicle ID, then packet
    ID. A packet that misses its deadline is discarded without occupying air
    time. Per-RSU service time is the payload bits divided by that link's
    Shannon capacity, supplied as ``serialization_seconds`` by the caller.
    """
    if not math.isfinite(observation_end_seconds) or observation_end_seconds <= 0:
        raise ValueError("observation_end_seconds must be finite and positive")
    grouped: dict[str, list[PacketAttempt]] = defaultdict(list)
    seen_ids: set[str] = set()
    for attempt in attempts:
        if attempt.packet_id in seen_ids:
            raise ValueError(f"duplicate packet_id: {attempt.packet_id}")
        if attempt.generated_at_seconds > observation_end_seconds:
            raise ValueError("packet generated after observation end")
        seen_ids.add(attempt.packet_id)
        grouped[attempt.rsu_id].append(attempt)

    outcomes = []
    for rsu_attempts in grouped.values():
        available_at = 0.0
        for attempt in sorted(
            rsu_attempts,
            key=lambda packet: (
                packet.generated_at_seconds, packet.vehicle_id, packet.packet_id
            ),
        ):
            start_at = max(attempt.generated_at_seconds, available_at)
            finish_at = start_at + attempt.serialization_seconds
            if finish_at <= attempt.deadline_seconds + 1e-12 and finish_at <= observation_end_seconds + 1e-12:
                outcomes.append(PacketOutcome(attempt, True, start_at, finish_at, None))
                available_at = finish_at
            else:
                reason = (
                    "deadline_miss"
                    if finish_at > attempt.deadline_seconds + 1e-12
                    else "observation_end"
                )
                outcomes.append(PacketOutcome(attempt, False, start_at, None, reason))
    return sorted(outcomes, key=lambda outcome: (
        outcome.attempt.generated_at_seconds,
        outcome.attempt.rsu_id,
        outcome.attempt.vehicle_id,
        outcome.attempt.packet_id,
    ))


def packet_delivery_ratio(outcomes: Iterable[PacketOutcome]) -> float:
    """Return delivered/attempted, or zero when there were no attempts."""
    packet_outcomes = list(outcomes)
    if not packet_outcomes:
        return 0.0
    return sum(outcome.delivered for outcome in packet_outcomes) / len(packet_outcomes)


def aggregate_metrics(
    outcomes: Iterable[PacketOutcome],
    observation_duration_seconds: float,
    rsu_ids: Iterable[str],
    bandwidth_hz: float,
    vehicle_active_seconds: Mapping[str, float],
) -> tuple[list[dict], list[dict]]:
    """Return per-RSU rows and a system row without duplicating capacity.

    Utilization is delivered telemetry serialization airtime divided by
    observation duration for each RSU. System utilization divides summed
    airtime by (number of independent RSUs * duration). Residual bandwidth is
    the channel bandwidth multiplied by its idle time fraction. The system
    transmission-rate field is an active-time-weighted aggregate: total packet
    attempts divided by total active vehicle-seconds.
    """
    if not math.isfinite(observation_duration_seconds) or observation_duration_seconds <= 0:
        raise ValueError("observation_duration_seconds must be positive")
    if not math.isfinite(bandwidth_hz) or bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be positive")
    rsus = list(rsu_ids)
    if not rsus or len(set(rsus)) != len(rsus):
        raise ValueError("rsu_ids must be nonempty and unique")
    outcome_list = list(outcomes)
    rows = []
    total_attempts = len(outcome_list)
    total_delivered = sum(result.delivered for result in outcome_list)
    total_busy = 0.0
    for rsu_id in rsus:
        subset = [result for result in outcome_list if result.attempt.rsu_id == rsu_id]
        busy_seconds = sum(
            result.attempt.serialization_seconds for result in subset if result.delivered
        )
        total_busy += busy_seconds
        utilization = 100.0 * busy_seconds / observation_duration_seconds
        rows.append({
            "scope": "rsu",
            "rsu_id": rsu_id,
            "attempted_packets": len(subset),
            "delivered_packets": sum(result.delivered for result in subset),
            "pdr": packet_delivery_ratio(subset),
            "utilization_percent": utilization,
            "residual_bandwidth_mhz": bandwidth_hz / 1_000_000 * (1.0 - utilization / 100.0),
        })
    system_utilization = 100.0 * total_busy / (len(rsus) * observation_duration_seconds)
    total_active_seconds = sum(vehicle_active_seconds.values())
    # Preserve the CSV field name, but this is a weighted system aggregate,
    # not an unweighted mean of per-vehicle rates.
    tx_per_vehicle_second = total_attempts / total_active_seconds if total_active_seconds else 0.0
    rows.append({
        "scope": "system",
        "rsu_id": "all",
        "attempted_packets": total_attempts,
        "delivered_packets": total_delivered,
        "pdr": total_delivered / total_attempts if total_attempts else 0.0,
        "utilization_percent": system_utilization,
        "residual_bandwidth_mhz": (
            len(rsus) * bandwidth_hz / 1_000_000 * (1.0 - system_utilization / 100.0)
        ),
        "mean_transmissions_per_vehicle_second": tx_per_vehicle_second,
    })

    counts = Counter(result.attempt.vehicle_id for result in outcome_list)
    vehicle_rows = [
        {
            "vehicle_id": vehicle_id,
            "active_seconds": active_seconds,
            "attempted_packets": counts[vehicle_id],
            "transmissions_per_second": counts[vehicle_id] / active_seconds,
        }
        for vehicle_id, active_seconds in sorted(vehicle_active_seconds.items())
    ]
    return rows, vehicle_rows


def analyze_saturation_thresholds(
    summary_rows: Iterable[Mapping[str, object]],
    tested_vehicle_counts: Iterable[int],
    threshold_percent: float = 60.0,
) -> list[dict]:
    """Find the first configured density exceeding a utilization threshold.

    Results remain separate by upload interval, path-loss exponent, scope, and
    RSU ID (for per-RSU rows). The configured vehicle-count order determines
    which crossing is reported first. A no-crossing group is returned
    explicitly with ``status='no_threshold'`` and a ``None`` crossing value.
    """
    def positive_integer_count(value: object, name: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a positive integer, not a boolean")
        if isinstance(value, str):
            try:
                count = int(value)
            except ValueError as error:
                raise ValueError(f"{name} must be a positive integer") from error
        elif isinstance(value, Integral):
            count = int(value)
        elif isinstance(value, Real):
            if not math.isfinite(value) or not float(value).is_integer():
                raise ValueError(f"{name} must be a finite integer")
            count = int(value)
        else:
            raise ValueError(f"{name} must be a positive integer")
        if count <= 0:
            raise ValueError(f"{name} must be greater than zero")
        return count

    def valid_utilization(value: object, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number from 0 to 100")
        try:
            utilization = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{name} must be a finite number from 0 to 100") from error
        if not math.isfinite(utilization) or not 0.0 <= utilization <= 100.0:
            raise ValueError(f"{name} must be finite and between 0 and 100 percent")
        return utilization

    counts = [
        positive_integer_count(value, "tested vehicle count")
        for value in tested_vehicle_counts
    ]
    if not counts or len(set(counts)) != len(counts):
        raise ValueError("tested_vehicle_counts must be nonempty and unique")
    if not math.isfinite(threshold_percent):
        raise ValueError("threshold_percent must be finite")

    grouped: dict[tuple[int, float, str, str], dict[int, float]] = defaultdict(dict)
    for row in summary_rows:
        scope = str(row.get("scope", ""))
        if scope not in ("system", "rsu"):
            continue
        rsu_id = str(row.get("rsu_id", "all")) if scope == "rsu" else "all"
        key = (
            int(row["upload_interval_ms"]),
            float(row["path_loss_exponent"]),
            scope,
            rsu_id,
        )
        vehicle_count = positive_integer_count(row["vehicle_count"], "row vehicle_count")
        if vehicle_count in grouped[key]:
            raise ValueError(f"duplicate threshold row for group {key}, count {vehicle_count}")
        grouped[key][vehicle_count] = valid_utilization(
            row["utilization_percent"], "row utilization_percent"
        )

    results = []
    for key in sorted(grouped):
        interval_ms, exponent, scope, rsu_id = key
        tested_values = grouped[key]
        first_crossing = next(
            (
                (count, tested_values[count])
                for count in counts
                if count in tested_values and tested_values[count] > threshold_percent
            ),
            None,
        )
        result = {
            "upload_interval_ms": interval_ms,
            "path_loss_exponent": exponent,
            "scope": scope,
            "rsu_id": rsu_id,
            "threshold_percent": float(threshold_percent),
            "tested_vehicle_counts": counts.copy(),
        }
        if first_crossing is None:
            result.update({
                "status": "no_threshold",
                "first_exceeding_vehicle_count": None,
                "first_exceeding_utilization_percent": None,
            })
        else:
            count, utilization = first_crossing
            result.update({
                "status": "threshold_crossed",
                "first_exceeding_vehicle_count": count,
                "first_exceeding_utilization_percent": utilization,
            })
        results.append(result)
    return results
