"""Unit-explicit V2I large-scale channel calculations.

The model uses a log-distance path-loss law with free-space loss at the
reference distance. It represents average received power, not fast fading,
interference, packet errors, or retransmissions.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

SPEED_OF_LIGHT_M_PER_S = 299_792_458.0
BOLTZMANN_NOISE_DENSITY_DBM_PER_HZ = -174.0


def _positive_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return number


@dataclass(frozen=True)
class ChannelParameters:
    """Link parameters; frequency is GHz, bandwidth is MHz, distances are m."""

    carrier_frequency_ghz: float = 5.9
    bandwidth_mhz: float = 10.0
    transmit_power_dbm: float = 20.0
    noise_density_dbm_per_hz: float = BOLTZMANN_NOISE_DENSITY_DBM_PER_HZ
    noise_figure_db: float = 9.0
    reference_distance_m: float = 1.0
    path_loss_exponent: float = 3.0
    payload_bytes: int = 32

    def __post_init__(self) -> None:
        for name in (
            "carrier_frequency_ghz", "bandwidth_mhz", "reference_distance_m",
            "path_loss_exponent",
        ):
            _positive_finite(name, getattr(self, name))
        for name in (
            "transmit_power_dbm", "noise_density_dbm_per_hz", "noise_figure_db"
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.noise_figure_db < 0:
            raise ValueError("noise_figure_db cannot be negative")
        if isinstance(self.payload_bytes, bool) or not isinstance(self.payload_bytes, int) or self.payload_bytes <= 0:
            raise ValueError("payload_bytes must be a positive integer")

    @property
    def bandwidth_hz(self) -> float:
        """Convert the configured MHz bandwidth explicitly to Hz."""
        return self.bandwidth_mhz * 1_000_000.0

    @property
    def reference_path_loss_db(self) -> float:
        """Free-space path loss at the configured reference distance."""
        frequency_hz = self.carrier_frequency_ghz * 1_000_000_000.0
        wavelength_m = SPEED_OF_LIGHT_M_PER_S / frequency_hz
        return 20.0 * math.log10(
            4.0 * math.pi * self.reference_distance_m / wavelength_m
        )


@dataclass(frozen=True)
class LinkBudget:
    distance_m: float
    path_loss_db: float
    received_power_dbm: float
    noise_power_dbm: float
    snr_db: float
    snr_linear: float
    shannon_capacity_bps: float
    payload_transmission_time_seconds: float


def log_distance_path_loss_db(
    distance_m: float,
    reference_path_loss_db: float,
    reference_distance_m: float = 1.0,
    path_loss_exponent: float = 3.0,
) -> float:
    """Return PL(d)=PL(d0)+10*n*log10(d/d0), requiring d >= d0."""
    distance = _positive_finite("distance_m", distance_m)
    reference_distance = _positive_finite("reference_distance_m", reference_distance_m)
    exponent = _positive_finite("path_loss_exponent", path_loss_exponent)
    if distance < reference_distance:
        raise ValueError("distance_m must be at least reference_distance_m")
    if isinstance(reference_path_loss_db, bool) or not isinstance(reference_path_loss_db, (int, float)) or not math.isfinite(reference_path_loss_db):
        raise ValueError("reference_path_loss_db must be finite")
    return float(reference_path_loss_db) + 10.0 * exponent * math.log10(distance / reference_distance)


def receiver_noise_power_dbm(
    bandwidth_hz: float,
    noise_figure_db: float,
    noise_density_dbm_per_hz: float = BOLTZMANN_NOISE_DENSITY_DBM_PER_HZ,
) -> float:
    """Integrate noise density over bandwidth and add receiver noise figure."""
    bandwidth = _positive_finite("bandwidth_hz", bandwidth_hz)
    if isinstance(noise_figure_db, bool) or not isinstance(noise_figure_db, (int, float)) or not math.isfinite(noise_figure_db) or noise_figure_db < 0:
        raise ValueError("noise_figure_db must be finite and nonnegative")
    if isinstance(noise_density_dbm_per_hz, bool) or not isinstance(noise_density_dbm_per_hz, (int, float)) or not math.isfinite(noise_density_dbm_per_hz):
        raise ValueError("noise_density_dbm_per_hz must be finite")
    return float(noise_density_dbm_per_hz) + 10.0 * math.log10(bandwidth) + float(noise_figure_db)


def snr_linear_from_db(snr_db: float) -> float:
    """Convert power ratio from dB to linear units."""
    if isinstance(snr_db, bool) or not isinstance(snr_db, (int, float)) or not math.isfinite(snr_db):
        raise ValueError("snr_db must be finite")
    return 10.0 ** (float(snr_db) / 10.0)


def shannon_capacity_bps(bandwidth_hz: float, snr_linear: float) -> float:
    """Theoretical Shannon capacity C=B*log2(1+SNR), in bits per second."""
    bandwidth = _positive_finite("bandwidth_hz", bandwidth_hz)
    if isinstance(snr_linear, bool) or not isinstance(snr_linear, (int, float)) or not math.isfinite(snr_linear) or snr_linear < 0:
        raise ValueError("snr_linear must be finite and nonnegative")
    return bandwidth * math.log2(1.0 + float(snr_linear))


def calculate_link_budget(distance_m: float, parameters: ChannelParameters) -> LinkBudget:
    """Calculate a single average V2I link budget for a distance >= d0."""
    path_loss = log_distance_path_loss_db(
        distance_m,
        parameters.reference_path_loss_db,
        parameters.reference_distance_m,
        parameters.path_loss_exponent,
    )
    received_power = parameters.transmit_power_dbm - path_loss
    noise_power = receiver_noise_power_dbm(
        parameters.bandwidth_hz,
        parameters.noise_figure_db,
        parameters.noise_density_dbm_per_hz,
    )
    snr_db = received_power - noise_power
    snr_linear = snr_linear_from_db(snr_db)
    return LinkBudget(
        distance_m=float(distance_m),
        path_loss_db=path_loss,
        received_power_dbm=received_power,
        noise_power_dbm=noise_power,
        snr_db=snr_db,
        snr_linear=snr_linear,
        shannon_capacity_bps=shannon_capacity_bps(parameters.bandwidth_hz, snr_linear),
        payload_transmission_time_seconds=(parameters.payload_bytes * 8) / shannon_capacity_bps(
            parameters.bandwidth_hz, snr_linear
        ),
    )


def nearest_rsu(
    vehicle_position_m: float,
    rsu_positions_m: Mapping[str, float],
) -> tuple[str, float]:
    """Return the nearest RSU ID and along-road distance in the 1-D scenario."""
    vehicle_position = vehicle_position_m
    if isinstance(vehicle_position, bool) or not isinstance(vehicle_position, (int, float)) or not math.isfinite(vehicle_position):
        raise ValueError("vehicle_position_m must be finite")
    if not rsu_positions_m:
        raise ValueError("at least one RSU position is required")
    for rsu_id, position in rsu_positions_m.items():
        if not isinstance(rsu_id, str) or not rsu_id:
            raise ValueError("RSU IDs must be nonempty strings")
        if isinstance(position, bool) or not isinstance(position, (int, float)) or not math.isfinite(position):
            raise ValueError(f"position for RSU {rsu_id!r} must be finite")
    rsu_id, rsu_position = min(
        rsu_positions_m.items(), key=lambda item: abs(vehicle_position - item[1])
    )
    return rsu_id, abs(vehicle_position - rsu_position)
