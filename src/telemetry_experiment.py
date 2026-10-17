"""Run the telemetry-density matrix, save CSV metrics, and plot results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from channel_model import ChannelParameters, calculate_link_budget, nearest_rsu
from sumo_smoke_test import DEFAULT_CONFIG, run_smoke_test
from telemetry_model import (
    PacketAttempt,
    aggregate_metrics,
    analyze_saturation_thresholds,
    schedule_shared_channel,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_CONFIG = PROJECT_ROOT / "config" / "telemetry_experiments.json"
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "results"


def make_packet_attempts(
    states: list[dict],
    upload_interval_ms: int,
    rsu_positions: dict[str, float],
    parameters: ChannelParameters,
    step_seconds: float,
    observation_end_seconds: float,
) -> tuple[list[PacketAttempt], dict[str, float]]:
    """Create periodic packets from SUMO states, sampled at upload times."""
    interval_seconds = upload_interval_ms / 1000.0
    per_vehicle: dict[str, list[dict]] = {}
    for state in states:
        per_vehicle.setdefault(state["vehicle_id"], []).append(state)

    attempts = []
    active_seconds = {}
    for vehicle_id, samples in per_vehicle.items():
        samples.sort(key=lambda row: row["time_seconds"])
        first_time = samples[0]["time_seconds"]
        last_time = samples[-1]["time_seconds"]
        active_seconds[vehicle_id] = last_time - first_time + step_seconds
        next_upload = first_time
        sequence = 0
        for sample in samples:
            sample_time = sample["time_seconds"]
            if sample_time + 1e-9 < next_upload:
                continue
            if next_upload >= observation_end_seconds - 1e-9:
                break
            rsu_id, physical_distance = nearest_rsu(
                sample["position_meters"], rsu_positions
            )
            modeled_distance = max(physical_distance, parameters.reference_distance_m)
            link = calculate_link_budget(modeled_distance, parameters)
            attempts.append(PacketAttempt(
                packet_id=f"{vehicle_id}-{upload_interval_ms}ms-{sequence}",
                vehicle_id=vehicle_id,
                rsu_id=rsu_id,
                generated_at_seconds=next_upload,
                deadline_seconds=next_upload + interval_seconds,
                serialization_seconds=(parameters.payload_bytes * 8)
                / link.shannon_capacity_bps,
            ))
            sequence += 1
            next_upload += interval_seconds
    return attempts, active_seconds


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows to write to {path}")
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _generate_plots(summary_rows: list[dict], output_directory: Path) -> list[Path]:
    system_rows = [row for row in summary_rows if row["scope"] == "system"]
    plot_specs = [
        ("utilization_percent", "Telemetry uplink utilization (%)", "telemetry_utilization.png"),
        ("pdr", "Packet delivery ratio", "telemetry_pdr.png"),
        ("residual_bandwidth_mhz", "Residual bandwidth across RSUs (MHz)", "telemetry_residual_bandwidth.png"),
        (
            "mean_transmissions_per_vehicle_second",
            "Weighted system rate (attempts / vehicle-second)",
            "telemetry_transmissions_per_second.png",
        ),
    ]
    plots = []
    for metric, ylabel, filename in plot_specs:
        fig, axis = plt.subplots(figsize=(9, 5.5))
        intervals = sorted({int(row["upload_interval_ms"]) for row in system_rows})
        exponents = sorted({float(row["path_loss_exponent"]) for row in system_rows})
        for interval in intervals:
            for exponent in exponents:
                subset = sorted(
                    (row for row in system_rows
                     if int(row["upload_interval_ms"]) == interval
                     and float(row["path_loss_exponent"]) == exponent),
                    key=lambda row: int(row["vehicle_count"]),
                )
                axis.plot(
                    [int(row["vehicle_count"]) for row in subset],
                    [float(row[metric]) for row in subset],
                    marker="o",
                    linestyle="-" if exponent == exponents[0] else "--",
                    label=f"{interval} ms, n={exponent:g}",
                )
        axis.set_xlabel("Number of vehicles")
        axis.set_ylabel(ylabel)
        if metric == "mean_transmissions_per_vehicle_second":
            axis.set_title("Weighted system transmission rate by density and upload interval")
        else:
            axis.set_title(f"{ylabel} by density and telemetry interval")
        axis.grid(True, alpha=0.3)
        axis.legend(title="Upload interval, path-loss exponent", fontsize=8)
        fig.tight_layout()
        path = output_directory / filename
        fig.savefig(path, dpi=160)
        plt.close(fig)
        plots.append(path)
    return plots


def run_experiments(
    config_path: Path = DEFAULT_EXPERIMENT_CONFIG,
    results_directory: Path = DEFAULT_RESULTS_DIR,
) -> tuple[list[dict], list[dict], list[Path]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    field_sizes = config["telemetry_fields_bytes"]
    payload_bytes = sum(field_sizes.values())
    if payload_bytes != 32:
        raise ValueError(f"Telemetry fields must total 32 bytes, got {payload_bytes}")
    if set(field_sizes) != {"position", "speed", "heading", "acceleration", "timestamp"}:
        raise ValueError("Telemetry field names do not match the specified schema")

    interval_values = [int(value) for value in config["upload_intervals_ms"]]
    vehicle_counts = [int(value) for value in config["vehicle_counts"]]
    exponents = [float(value) for value in config["path_loss_exponents"]]
    if any(value not in (3.0, 3.5) for value in exponents):
        raise ValueError("Supported path-loss exponents are 3.0 and 3.5")

    channel_config = dict(config["channel"])
    channel_config["payload_bytes"] = payload_bytes
    step_seconds = float(config["sumo_step_seconds"])
    duration_seconds = float(config["sumo_duration_seconds"])
    departure_spacing = float(config["vehicle_departure_spacing_seconds"])
    distribution_fraction = float(config["vehicle_initial_distribution_fraction"])
    rsu_positions = {
        f"rsu{index + 1}": float(position)
        for index, position in enumerate(
            json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))["rsu_positions_meters"]
        )
    }
    if any(interval <= 0 for interval in interval_values) or any(count <= 0 for count in vehicle_counts):
        raise ValueError("Vehicle counts and upload intervals must be positive")

    results_directory.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    vehicle_rows = []
    smoke_config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    steps = math.ceil(duration_seconds / step_seconds)
    for vehicle_count in vehicle_counts:
        scenario_config = dict(smoke_config)
        scenario_config.update({
            "vehicle_count": vehicle_count,
            "simulation_steps": steps,
            "step_length_seconds": step_seconds,
            "vehicle_departure_spacing_seconds": departure_spacing,
            "vehicle_start_positions_meters": [
                (scenario_config["road_length_meters"] * distribution_fraction)
                * index / max(vehicle_count - 1, 1)
                for index in range(vehicle_count)
            ],
        })
        with tempfile.TemporaryDirectory(prefix="cc_telemetry_") as temporary_directory:
            scenario_path = Path(temporary_directory) / "scenario.json"
            scenario_path.write_text(json.dumps(scenario_config), encoding="utf-8")
            print(f"Simulating {vehicle_count} vehicles at {step_seconds:g}s SUMO steps...")
            states = run_smoke_test(scenario_path)

        observation_end = steps * step_seconds
        for exponent in exponents:
            parameters = ChannelParameters(
                **channel_config,
                path_loss_exponent=exponent,
            )
            for interval_ms in interval_values:
                attempts, active_seconds = make_packet_attempts(
                    states, interval_ms, rsu_positions, parameters, step_seconds,
                    observation_end,
                )
                outcomes = schedule_shared_channel(attempts, observation_end)
                aggregate_rows, per_vehicle_rows = aggregate_metrics(
                    outcomes,
                    observation_end,
                    rsu_positions.keys(),
                    parameters.bandwidth_hz,
                    active_seconds,
                )
                common = {
                    "vehicle_count": vehicle_count,
                    "upload_interval_ms": interval_ms,
                    "path_loss_exponent": exponent,
                    "payload_bytes": payload_bytes,
                    "observation_duration_seconds": observation_end,
                }
                for row in aggregate_rows:
                    summary_rows.append({**common, **row})
                for row in per_vehicle_rows:
                    vehicle_rows.append({**common, **row})

    summary_path = results_directory / "telemetry_summary.csv"
    vehicle_path = results_directory / "telemetry_per_vehicle.csv"
    _write_csv(summary_path, summary_rows)
    _write_csv(vehicle_path, vehicle_rows)
    plot_paths = _generate_plots(summary_rows, results_directory)
    return summary_rows, vehicle_rows, [summary_path, vehicle_path, *plot_paths]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_EXPERIMENT_CONFIG)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument(
        "--plots-only",
        action="store_true",
        help="Regenerate plots from an existing telemetry_summary.csv without SUMO",
    )
    args = parser.parse_args()
    if args.plots_only:
        summary_path = args.results_dir / "telemetry_summary.csv"
        with summary_path.open(newline="", encoding="utf-8") as source:
            saved_summary = list(csv.DictReader(source))
        plots = _generate_plots(saved_summary, args.results_dir)
        print("Generated plots:")
        for path in plots:
            print(f"  {path}")
        return
    summary_rows, _, output_paths = run_experiments(args.config, args.results_dir)
    experiment_config = json.loads(args.config.read_text(encoding="utf-8"))
    threshold_results = analyze_saturation_thresholds(
        summary_rows, experiment_config["vehicle_counts"], threshold_percent=60.0
    )
    print("Utilization threshold analysis (>60.0%; first tested density per group):")
    for result in threshold_results:
        scope = (
            "system-wide" if result["scope"] == "system"
            else f"RSU {result['rsu_id']}"
        )
        label = (
            f"upload={result['upload_interval_ms']} ms, "
            f"n={result['path_loss_exponent']:g}, {scope}"
        )
        if result["status"] == "no_threshold":
            counts = ", ".join(str(value) for value in result["tested_vehicle_counts"])
            print(f"  {label}: no threshold among tested counts [{counts}]")
        else:
            print(
                f"  {label}: first crossing at "
                f"{result['first_exceeding_vehicle_count']} vehicles "
                f"({result['first_exceeding_utilization_percent']:.3f}%)"
            )
    print(f"Completed {sum(row['scope'] == 'system' for row in summary_rows)} system configurations.")
    print("Generated outputs:")
    for path in output_paths:
        print(f"  {path}")


if __name__ == "__main__":
    main()
