import xml.etree.ElementTree as ET
import json

import pytest

from src.sumo_gui_demo import (
    DEFAULT_DURATION_SECONDS,
    GUI_STEP_DELAY_MS,
    build_demo,
    gui_command,
    write_demo_inputs,
)


DEMO_CONFIG = {
    "vehicle_count": 3,
    "step_length_seconds": 0.05,
    "road_length_meters": 1000,
    "vehicle_departure_spacing_seconds": 2,
    "rsu_positions_meters": [250, 500, 750],
}


def test_write_demo_inputs_creates_one_road_three_rsus_and_configurable_vehicles(tmp_path):
    paths = write_demo_inputs(tmp_path, DEMO_CONFIG)

    nodes = ET.parse(paths["nodes"]).getroot()
    edges = ET.parse(paths["edges"]).getroot()
    routes = ET.parse(paths["routes"]).getroot()
    additional = ET.parse(paths["additional"]).getroot()
    sumocfg = ET.parse(paths["config"]).getroot()
    assert len(nodes.findall("node")) == 2
    assert len(edges.findall("edge")) == 1
    assert len(routes.findall("vehicle")) == 3
    assert [poi.attrib["id"] for poi in additional.findall("poi")] == [
        "RSU1_250m", "RSU2_500m", "RSU3_750m"
    ]
    assert sumocfg.find("./input/net-file").attrib["value"] == "road.net.xml"
    assert sumocfg.find("./input/additional-files").attrib["value"] == "rsus.add.xml"
    assert sumocfg.find("./time/end").attrib["value"] == str(DEFAULT_DURATION_SECONDS)


def test_gui_command_starts_playback_at_realtime_pace_and_does_not_enable_quit(tmp_path):
    command = gui_command("sumo-gui.exe", tmp_path / "demo.sumocfg")

    assert command == [
        "sumo-gui.exe", "-c", str(tmp_path / "demo.sumocfg"),
        "--start", "--delay", "50",
    ]
    assert "--quit-on-end" not in command


def test_build_demo_sets_step_length_to_match_gui_realtime_delay(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({**DEMO_CONFIG, "step_length_seconds": 1.0}), encoding="utf-8")
    monkeypatch.setattr("src.sumo_gui_demo.subprocess.run", lambda *args, **kwargs: None)

    generated_config = build_demo(config_path, tmp_path / "demo", None, 300)
    root = ET.parse(generated_config).getroot()

    assert root.find("./time/step-length").attrib["value"] == str(GUI_STEP_DELAY_MS / 1000.0)
    assert root.find("./time/end").attrib["value"] == "300"


@pytest.mark.parametrize("count", [0, -1, True, 2.5])
def test_demo_rejects_invalid_vehicle_count(tmp_path, count):
    config = {**DEMO_CONFIG, "vehicle_count": count}
    with pytest.raises(ValueError, match="positive integer"):
        write_demo_inputs(tmp_path, config)


def test_demo_rejects_wrong_rsu_count(tmp_path):
    config = {**DEMO_CONFIG, "rsu_positions_meters": [250, 500]}
    with pytest.raises(ValueError, match="exactly three"):
        write_demo_inputs(tmp_path, config)
