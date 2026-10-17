import pytest

from src.telemetry_model import (
    PacketAttempt,
    aggregate_metrics,
    analyze_saturation_thresholds,
    packet_delivery_ratio,
    schedule_shared_channel,
)


def packet(packet_id, vehicle, rsu, time, deadline, airtime):
    return PacketAttempt(packet_id, vehicle, rsu, time, deadline, airtime)


def test_concurrent_packets_are_serialized_in_stable_fcfs_order():
    attempts = [
        packet("b-packet", "veh-b", "rsu1", 0, 0.1, 0.02),
        packet("a-packet", "veh-a", "rsu1", 0, 0.1, 0.02),
    ]
    outcomes = schedule_shared_channel(attempts, 1.0)
    by_id = {outcome.attempt.packet_id: outcome for outcome in outcomes}
    assert by_id["a-packet"].delivered
    assert by_id["a-packet"].completed_at_seconds == pytest.approx(0.02)
    assert by_id["b-packet"].delivered
    assert by_id["b-packet"].queued_at_seconds == pytest.approx(0.02)
    assert by_id["b-packet"].completed_at_seconds == pytest.approx(0.04)


def test_packet_is_lost_when_capacity_limited_queue_misses_deadline():
    attempts = [
        packet("first", "veh-a", "rsu1", 0, 0.1, 0.04),
        packet("late", "veh-b", "rsu1", 0, 0.02, 0.03),
    ]
    outcomes = schedule_shared_channel(attempts, 1.0)
    assert {outcome.attempt.packet_id: outcome.delivered for outcome in outcomes} == {
        "first": True,
        "late": False,
    }
    assert next(outcome for outcome in outcomes if not outcome.delivered).loss_reason == "deadline_miss"


def test_packet_delivery_ratio_uses_delivered_over_attempted():
    outcomes = schedule_shared_channel([
        packet("ok", "veh-a", "rsu1", 0, 1, 0.1),
        packet("lost", "veh-b", "rsu1", 0, 0.05, 0.1),
    ], 1.0)
    assert packet_delivery_ratio(outcomes) == pytest.approx(0.5)
    assert packet_delivery_ratio([]) == 0.0


def test_utilization_residual_bandwidth_and_scope_aggregation():
    attempts = [
        packet("delivered-1", "veh-a", "rsu1", 0.0, 0.1, 0.04),
        packet("deadline-drop", "veh-b", "rsu1", 0.0, 0.02, 0.03),
        packet("delivered-2", "veh-b", "rsu1", 0.1, 0.2, 0.02),
    ]
    outcomes = schedule_shared_channel(attempts, 1.0)
    rsu_rows, vehicle_rows = aggregate_metrics(
        outcomes,
        observation_duration_seconds=1.0,
        rsu_ids=["rsu1", "rsu2"],
        bandwidth_hz=10_000_000,
        vehicle_active_seconds={"veh-a": 1.0, "veh-b": 1.0},
    )
    rsu1 = next(row for row in rsu_rows if row["rsu_id"] == "rsu1")
    rsu2 = next(row for row in rsu_rows if row["rsu_id"] == "rsu2")
    system = next(row for row in rsu_rows if row["scope"] == "system")
    assert rsu1["utilization_percent"] == pytest.approx(6.0)
    assert rsu1["residual_bandwidth_mhz"] == pytest.approx(9.4)
    assert rsu1["pdr"] == pytest.approx(2 / 3)
    assert rsu2["utilization_percent"] == 0
    assert rsu2["residual_bandwidth_mhz"] == pytest.approx(10.0)
    assert system["utilization_percent"] == pytest.approx(3.0)
    assert system["residual_bandwidth_mhz"] == pytest.approx(19.4)
    assert system["mean_transmissions_per_vehicle_second"] == pytest.approx(1.5)
    assert {row["vehicle_id"] for row in vehicle_rows} == {"veh-a", "veh-b"}
    assert next(row for row in vehicle_rows if row["vehicle_id"] == "veh-a")["transmissions_per_second"] == pytest.approx(1.0)


def test_system_transmission_rate_is_active_time_weighted_not_unweighted_mean():
    outcomes = schedule_shared_channel([
        packet("a-1", "veh-a", "rsu1", 0.0, 0.5, 0.01),
        packet("b-1", "veh-b", "rsu1", 0.1, 0.5, 0.01),
        packet("b-2", "veh-b", "rsu1", 0.2, 0.6, 0.01),
    ], 1.0)
    summary_rows, vehicle_rows = aggregate_metrics(
        outcomes,
        observation_duration_seconds=1.0,
        rsu_ids=["rsu1"],
        bandwidth_hz=10_000_000,
        vehicle_active_seconds={"veh-a": 0.75, "veh-b": 1.0},
    )

    system = next(row for row in summary_rows if row["scope"] == "system")
    per_vehicle = {row["vehicle_id"]: row["transmissions_per_second"] for row in vehicle_rows}
    arithmetic_mean = sum(per_vehicle.values()) / len(per_vehicle)
    assert per_vehicle["veh-a"] == pytest.approx(1 / 0.75)
    assert per_vehicle["veh-b"] == pytest.approx(2.0)
    assert system["mean_transmissions_per_vehicle_second"] == pytest.approx(3 / 1.75)
    assert system["mean_transmissions_per_vehicle_second"] != pytest.approx(arithmetic_mean)


def test_packets_on_independent_rsu_channels_do_not_queue_each_other():
    outcomes = schedule_shared_channel([
        packet("p1", "veh-a", "rsu1", 0, 0.1, 0.08),
        packet("p2", "veh-b", "rsu2", 0, 0.1, 0.08),
    ], 1.0)
    assert all(outcome.delivered for outcome in outcomes)
    assert all(outcome.queued_at_seconds == 0 for outcome in outcomes)


def test_invalid_attempt_and_duplicate_packet_are_rejected():
    with pytest.raises(ValueError):
        packet("bad", "veh", "rsu", 1, 1, 0.01)
    duplicate = packet("same", "veh", "rsu1", 0, 1, 0.01)
    with pytest.raises(ValueError, match="duplicate"):
        schedule_shared_channel([duplicate, duplicate], 1.0)


def test_threshold_comparison_is_strict_at_exactly_sixty_percent():
    result = analyze_saturation_thresholds([{
        "vehicle_count": 5,
        "upload_interval_ms": 50,
        "path_loss_exponent": 3.0,
        "scope": "system",
        "rsu_id": "all",
        "utilization_percent": 60.0,
    }], [5])[0]
    assert result["status"] == "no_threshold"
    assert result["first_exceeding_vehicle_count"] is None


@pytest.mark.parametrize("invalid_count", [0, -1, True, 5.7, float("nan"), float("inf"), -float("inf")])
def test_threshold_rejects_invalid_configured_vehicle_counts(invalid_count):
    with pytest.raises(ValueError, match="vehicle count"):
        analyze_saturation_thresholds([], [invalid_count])


def test_threshold_rejects_fractional_vehicle_count_in_summary_row():
    row = {
        "vehicle_count": 5.7,
        "upload_interval_ms": 50,
        "path_loss_exponent": 3.0,
        "scope": "system",
        "rsu_id": "all",
        "utilization_percent": 50.0,
    }
    with pytest.raises(ValueError, match="vehicle_count"):
        analyze_saturation_thresholds([row], [5])


@pytest.mark.parametrize("invalid_utilization", [
    None, True, "not-a-number", float("nan"), float("inf"), -float("inf"), -0.01, 100.01,
])
def test_threshold_rejects_invalid_utilization_values(invalid_utilization):
    row = {
        "vehicle_count": 5,
        "upload_interval_ms": 50,
        "path_loss_exponent": 3.0,
        "scope": "system",
        "rsu_id": "all",
        "utilization_percent": invalid_utilization,
    }
    with pytest.raises(ValueError, match="utilization_percent"):
        analyze_saturation_thresholds([row], [5])


def test_threshold_detects_utilization_above_sixty_percent():
    result = analyze_saturation_thresholds([{
        "vehicle_count": 5,
        "upload_interval_ms": 50,
        "path_loss_exponent": 3.0,
        "scope": "system",
        "rsu_id": "all",
        "utilization_percent": 60.001,
    }], [5])[0]
    assert result["status"] == "threshold_crossed"
    assert result["first_exceeding_vehicle_count"] == 5


def test_threshold_selects_first_qualifying_tested_density():
    rows = [
        {"vehicle_count": count, "upload_interval_ms": 50,
         "path_loss_exponent": 3.0, "scope": "system", "rsu_id": "all",
         "utilization_percent": utilization}
        for count, utilization in [(5, 59.9), (10, 60.0), (20, 60.1), (50, 80.0)]
    ]
    result = analyze_saturation_thresholds(rows, [5, 10, 20, 50])[0]
    assert result["first_exceeding_vehicle_count"] == 20
    assert result["first_exceeding_utilization_percent"] == pytest.approx(60.1)


def test_threshold_respects_deliberately_non_sorted_configured_count_order():
    rows = [
        {"vehicle_count": count, "upload_interval_ms": 50,
         "path_loss_exponent": 3.0, "scope": "system", "rsu_id": "all",
         "utilization_percent": utilization}
        for count, utilization in [(5, 80.0), (10, 70.0), (20, 50.0)]
    ]
    result = analyze_saturation_thresholds(rows, [10, 5, 20])[0]
    assert result["first_exceeding_vehicle_count"] == 10
    assert result["first_exceeding_utilization_percent"] == pytest.approx(70.0)


def test_threshold_returns_explicit_no_crossing_result():
    rows = [
        {"vehicle_count": count, "upload_interval_ms": 50,
         "path_loss_exponent": 3.0, "scope": "system", "rsu_id": "all",
         "utilization_percent": utilization}
        for count, utilization in [(5, 10.0), (10, 30.0), (20, 60.0)]
    ]
    result = analyze_saturation_thresholds(rows, [5, 10, 20])[0]
    assert result["status"] == "no_threshold"
    assert result["first_exceeding_vehicle_count"] is None
    assert result["first_exceeding_utilization_percent"] is None


def test_threshold_keeps_upload_exponent_and_rsu_scopes_separate():
    base = {"vehicle_count": 10, "upload_interval_ms": 50,
            "path_loss_exponent": 3.0}
    rows = [
        {**base, "scope": "system", "rsu_id": "all", "utilization_percent": 20},
        {**base, "scope": "rsu", "rsu_id": "rsu1", "utilization_percent": 70},
        {**base, "scope": "rsu", "rsu_id": "rsu2", "utilization_percent": 80},
        {**base, "upload_interval_ms": 100, "scope": "system",
         "rsu_id": "all", "utilization_percent": 90},
        {**base, "path_loss_exponent": 3.5, "scope": "system",
         "rsu_id": "all", "utilization_percent": 95},
    ]
    results = analyze_saturation_thresholds(rows, [5, 10, 20])
    by_key = {
        (r["upload_interval_ms"], r["path_loss_exponent"], r["scope"], r["rsu_id"]): r
        for r in results
    }
    assert len(results) == 5
    assert by_key[(50, 3.0, "system", "all")]["status"] == "no_threshold"
    assert by_key[(50, 3.0, "rsu", "rsu1")]["first_exceeding_vehicle_count"] == 10
    assert by_key[(50, 3.0, "rsu", "rsu2")]["first_exceeding_vehicle_count"] == 10
    assert by_key[(100, 3.0, "system", "all")]["first_exceeding_vehicle_count"] == 10
    assert by_key[(50, 3.5, "system", "all")]["first_exceeding_vehicle_count"] == 10


def test_observation_end_drop_counts_attempt_but_no_delivery_or_airtime():
    attempt = packet("end-boundary", "veh-a", "rsu1", 0.0, 0.1, 0.06)
    outcomes = schedule_shared_channel([attempt], observation_end_seconds=0.05)
    assert len(outcomes) == 1
    assert not outcomes[0].delivered
    assert outcomes[0].loss_reason == "observation_end"
    assert outcomes[0].completed_at_seconds is None
    assert packet_delivery_ratio(outcomes) == 0.0

    rsu_rows, _ = aggregate_metrics(
        outcomes,
        observation_duration_seconds=0.05,
        rsu_ids=["rsu1"],
        bandwidth_hz=10_000_000,
        vehicle_active_seconds={"veh-a": 0.05},
    )
    rsu = next(row for row in rsu_rows if row["scope"] == "rsu")
    system = next(row for row in rsu_rows if row["scope"] == "system")
    for row in (rsu, system):
        assert row["attempted_packets"] == 1
        assert row["delivered_packets"] == 0
        assert row["pdr"] == 0.0
        assert row["utilization_percent"] == 0.0
    assert rsu["residual_bandwidth_mhz"] == pytest.approx(10.0)
