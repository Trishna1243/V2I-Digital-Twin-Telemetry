"""Create and launch a small, persistent SUMO-GUI roadside-unit demo."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import sumolib

try:  # Support both direct script execution and package-style test imports.
    from .sumo_smoke_test import DEFAULT_CONFIG
except ImportError:
    from sumo_smoke_test import DEFAULT_CONFIG


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "sumo_demo"
DEFAULT_DURATION_SECONDS = 300
GUI_STEP_DELAY_MS = 50


def _validated_config(config: dict, vehicle_count: int | None = None) -> dict:
    result = dict(config)
    count = result["vehicle_count"] if vehicle_count is None else vehicle_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("vehicle_count must be a positive integer")
    positions = result["rsu_positions_meters"]
    if len(positions) != 3:
        raise ValueError("The GUI demo expects exactly three RSU positions")
    road_length = float(result["road_length_meters"])
    if road_length <= 0 or any(float(position) < 0 or float(position) > road_length for position in positions):
        raise ValueError("RSU positions must be within the positive road length")
    spacing = float(result["vehicle_departure_spacing_seconds"])
    if spacing < 0:
        raise ValueError("vehicle_departure_spacing_seconds cannot be negative")
    result["vehicle_count"] = count
    return result


def write_demo_inputs(
    directory: Path,
    config: dict,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
) -> dict[str, Path]:
    """Write inspectable SUMO inputs; this pure file-generation step needs no GUI."""
    config = _validated_config(config)
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "nodes": directory / "nodes.nod.xml",
        "edges": directory / "edges.edg.xml",
        "network": directory / "road.net.xml",
        "routes": directory / "vehicles.rou.xml",
        "additional": directory / "rsus.add.xml",
        "config": directory / "demo.sumocfg",
    }
    road_length = float(config["road_length_meters"])
    paths["nodes"].write_text(
        '<nodes><node id="start" x="0" y="0" type="priority"/>'
        f'<node id="end" x="{road_length:g}" y="0" type="priority"/></nodes>',
        encoding="utf-8",
    )
    paths["edges"].write_text(
        '<edges><edge id="road" from="start" to="end" '
        'numLanes="1" speed="13.89"/></edges>', encoding="utf-8",
    )
    vehicle_lines = "\n".join(
        f'<vehicle id="veh{i}" type="passenger" route="straight" '
        f'depart="{i * float(config["vehicle_departure_spacing_seconds"]):g}" />'
        for i in range(config["vehicle_count"])
    )
    paths["routes"].write_text(
        '<routes><vType id="passenger" accel="2.6" decel="4.5" '
        'length="5" maxSpeed="13.89"/><route id="straight" edges="road"/>'
        f'{vehicle_lines}</routes>', encoding="utf-8",
    )
    colors = ("red", "blue", "green")
    poi_lines = "\n".join(
        f'<poi id="RSU{i + 1}_{float(position):g}m" type="RSU" '
        f'color="{colors[i]}" x="{float(position):g}" y="4" layer="10"/>'
        for i, position in enumerate(config["rsu_positions_meters"])
    )
    paths["additional"].write_text(f"<additional>{poi_lines}</additional>", encoding="utf-8")
    paths["config"].write_text(
        '<configuration><input>'
        '<net-file value="road.net.xml"/><route-files value="vehicles.rou.xml"/>'
        '<additional-files value="rsus.add.xml"/>'
        '</input><time><begin value="0"/><end value="'
        f'{float(duration_seconds):g}"/><step-length value="'
        f'{float(config["step_length_seconds"]):g}"/></time>'
        '<processing><time-to-teleport value="-1"/></processing></configuration>',
        encoding="utf-8",
    )
    return paths


def build_demo(config_path: Path, output_directory: Path, vehicle_count: int | None, duration: float) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if vehicle_count is not None:
        config["vehicle_count"] = vehicle_count
    # A 50 ms simulation step paired with --delay 50 gives approximately
    # real-time playback while keeping the road, vehicle type, and RSUs intact.
    config["step_length_seconds"] = GUI_STEP_DELAY_MS / 1000.0
    paths = write_demo_inputs(output_directory, config, duration)
    subprocess.run(
        [sumolib.checkBinary("netconvert"), "--node-files", str(paths["nodes"]),
         "--edge-files", str(paths["edges"]), "--output-file", str(paths["network"])],
        check=True, capture_output=True, text=True,
    )
    return paths["config"]


def gui_command(sumo_gui_binary: str, config_path: Path) -> list[str]:
    """Start GUI playback near real time and leave end-of-run closing disabled.

    SUMO's --quit-on-end option is a flag that enables closing; it does not
    accept a ``false`` argument. Omitting it preserves the default open window.
    """
    return [
        sumo_gui_binary,
        "-c", str(config_path),
        "--start",
        "--delay", str(GUI_STEP_DELAY_MS),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicles", type=int, help="vehicle count (default: smoke-test config)")
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION_SECONDS,
        help="simulation duration in seconds (default: 300)",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    config_path = build_demo(args.config, args.output_dir, args.vehicles, args.duration)
    command = gui_command(sumolib.checkBinary("sumo-gui"), config_path)
    print(f"Launching SUMO-GUI with persistent files in {args.output_dir.resolve()}")
    print("RSU markers are visual POIs; they do not affect the channel model.")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
