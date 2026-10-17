"""Minimal SUMO + TraCI check for the CC project."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import sumolib

# The pip-installed TraCI package checks SUMO_HOME for XML schemas. Derive it
# from the SUMO executable when Windows has SUMO on PATH but no SUMO_HOME set.
if not os.environ.get("SUMO_HOME"):
    sumo_executable = shutil.which("sumo")
    if sumo_executable:
        os.environ["SUMO_HOME"] = str(Path(sumo_executable).resolve().parent.parent)

import traci


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "smoke_test.json"


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as config_file:
        config = json.load(config_file)
    if config["vehicle_count"] < 1:
        raise ValueError("vehicle_count must be at least 1")
    if len(config["rsu_positions_meters"]) != 3:
        raise ValueError("The smoke test expects exactly three RSU positions")
    return config


def write_network_files(directory: Path, config: dict) -> Path:
    """Build one straight road using SUMO's installed netconvert utility."""
    nodes = directory / "nodes.nod.xml"
    edges = directory / "edges.edg.xml"
    network = directory / "road.net.xml"

    road_length = int(config["road_length_meters"])
    nodes.write_text(
        '<nodes><node id="start" x="0" y="0" type="priority"/>'
        f'<node id="end" x="{road_length}" y="0" type="priority"/></nodes>',
        encoding="utf-8",
    )
    edges.write_text(
        '<edges><edge id="road" from="start" to="end" '
        'numLanes="1" speed="13.89"/></edges>',
        encoding="utf-8",
    )

    netconvert = sumolib.checkBinary("netconvert")
    subprocess.run(
        [netconvert, "--node-files", str(nodes), "--edge-files", str(edges),
         "--output-file", str(network)],
        check=True,
        capture_output=True,
        text=True,
    )
    return network


def write_routes(directory: Path, config: dict) -> Path:
    routes = directory / "vehicles.rou.xml"
    count = int(config["vehicle_count"])
    spacing = float(config["vehicle_departure_spacing_seconds"])
    start_positions = config.get("vehicle_start_positions_meters")
    if start_positions is not None and len(start_positions) != count:
        raise ValueError("vehicle_start_positions_meters must match vehicle_count")
    vehicle_lines = []
    for index in range(count):
        if start_positions is None:
            vehicle_lines.append(
                f'<vehicle id="veh{index}" type="passenger" route="straight" '
                f'depart="{index * spacing:g}" />'
            )
        else:
            vehicle_lines.append(
                f'<vehicle id="veh{index}" type="passenger" route="straight" '
                f'depart="0" departPos="{float(start_positions[index]):.3f}" '
                'departSpeed="0" />'
            )
    vehicles = "\n".join(vehicle_lines)
    routes.write_text(
        '<routes><vType id="passenger" accel="2.6" decel="4.5" '
        'length="5" maxSpeed="13.89"/><route id="straight" edges="road"/>'
        f'{vehicles}</routes>',
        encoding="utf-8",
    )
    return routes


def run_smoke_test(config_path: Path = DEFAULT_CONFIG) -> list[dict]:
    config = load_config(config_path)
    sumo_binary = sumolib.checkBinary("sumo")

    with tempfile.TemporaryDirectory(prefix="cc_sumo_smoke_") as temp_dir:
        directory = Path(temp_dir)
        network = write_network_files(directory, config)
        routes = write_routes(directory, config)
        command = [
            sumo_binary,
            "--net-file", str(network),
            "--route-files", str(routes),
            "--step-length", str(config["step_length_seconds"]),
            "--no-step-log", "true",
            "--duration-log.disable", "true",
        ]

        states: list[dict] = []
        traci.start(command)
        try:
            for _ in range(int(config["simulation_steps"])):
                traci.simulationStep()
                for vehicle_id in traci.vehicle.getIDList():
                    states.append(
                        {
                            "time_seconds": traci.simulation.getTime(),
                            "vehicle_id": vehicle_id,
                            "position_meters": traci.vehicle.getPosition(vehicle_id)[0],
                            "speed_meters_per_second": traci.vehicle.getSpeed(vehicle_id),
                            "road_id": traci.vehicle.getRoadID(vehicle_id),
                        }
                    )
        finally:
            traci.close()

    if not states:
        raise RuntimeError("SUMO ran but TraCI returned no vehicle states")
    observed_ids = {state["vehicle_id"] for state in states}
    expected_ids = {f"veh{index}" for index in range(int(config["vehicle_count"]))}
    if observed_ids != expected_ids:
        raise RuntimeError(
            f"Expected vehicles {sorted(expected_ids)}, observed {sorted(observed_ids)}"
        )
    return states


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    states = run_smoke_test(args.config)
    final_by_vehicle = {}
    for state in states:
        final_by_vehicle[state["vehicle_id"]] = state
    print(f"SUMO + TraCI smoke test passed: {len(final_by_vehicle)} vehicles observed")
    for vehicle_id in sorted(final_by_vehicle):
        state = final_by_vehicle[vehicle_id]
        print(
            f"{vehicle_id}: t={state['time_seconds']:.0f}s, "
            f"x={state['position_meters']:.1f}m, "
            f"speed={state['speed_meters_per_second']:.2f}m/s, "
            f"road={state['road_id']}"
        )
    print(f"Configured RSUs at: {load_config(args.config)['rsu_positions_meters']} m")


if __name__ == "__main__":
    main()
