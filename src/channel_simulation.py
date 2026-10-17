"""Run the smoke scenario and print per-vehicle nearest-RSU link estimates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from channel_model import ChannelParameters, calculate_link_budget, nearest_rsu
from sumo_smoke_test import DEFAULT_CONFIG, run_smoke_test


def run_channel_simulation(config_path: Path, path_loss_exponent: float = 3.0) -> list[dict]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rsu_positions = {
        f"rsu{index + 1}": float(position)
        for index, position in enumerate(config["rsu_positions_meters"])
    }
    parameters = ChannelParameters(path_loss_exponent=path_loss_exponent)
    states = run_smoke_test(config_path)
    results = []
    for state in states:
        rsu_id, physical_distance = nearest_rsu(state["position_meters"], rsu_positions)
        # This large-scale model is defined for d >= d0. Clamp a closer
        # simulated position to d0 (1 m by default) rather than extrapolating.
        modeled_distance = max(physical_distance, parameters.reference_distance_m)
        budget = calculate_link_budget(modeled_distance, parameters)
        results.append({
            "time_seconds": state["time_seconds"],
            "vehicle_id": state["vehicle_id"],
            "nearest_rsu_id": rsu_id,
            "physical_distance_m": physical_distance,
            "modeled_distance_m": modeled_distance,
            "received_power_dbm": budget.received_power_dbm,
            "noise_power_dbm": budget.noise_power_dbm,
            "snr_db": budget.snr_db,
            "snr_linear": budget.snr_linear,
            "shannon_capacity_bps": budget.shannon_capacity_bps,
            "payload_transmission_time_seconds": budget.payload_transmission_time_seconds,
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--path-loss-exponent", type=float, choices=(3.0, 3.5), default=3.0)
    args = parser.parse_args()
    results = run_channel_simulation(args.config, args.path_loss_exponent)
    final_state = {}
    for result in results:
        final_state[result["vehicle_id"]] = result
    print(f"Channel run complete: {len(results)} vehicle-time samples; n={args.path_loss_exponent}")
    for vehicle_id, result in sorted(final_state.items()):
        print(
            f"{vehicle_id} -> {result['nearest_rsu_id']} at "
            f"{result['physical_distance_m']:.1f} m: "
            f"Pr={result['received_power_dbm']:.2f} dBm, "
            f"SNR={result['snr_db']:.2f} dB, "
            f"C={result['shannon_capacity_bps'] / 1_000_000:.3f} Mbps, "
            f"ideal 32-byte serialization="
            f"{result['payload_transmission_time_seconds'] * 1_000:.3f} ms"
        )


if __name__ == "__main__":
    main()
