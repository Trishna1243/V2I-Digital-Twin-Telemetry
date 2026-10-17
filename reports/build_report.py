"""Build the assignment report PDF from the workspace's saved CSVs and plots.

Run from the project root with:
    .\\.venv\\Scripts\\python.exe reports\\build_report.py

The script only reads results/ and writes the PDF plus page-preview PNGs under
reports/qa/. It does not run SUMO or edit experiment outputs.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
QA = REPORTS / "qa"
PDF_PATH = REPORTS / "V2I_Digital_Twin_Telemetry_Overhead_Report.pdf"
SUMMARY_CSV = ROOT / "results" / "telemetry_summary.csv"
VEHICLE_CSV = ROOT / "results" / "telemetry_per_vehicle.csv"
PLOT_FILES = [
    ("Utilization", "telemetry_utilization.png", "System-wide telemetry utilization is the average busy fraction across the three independent 10 MHz RSU channels."),
    ("Packet delivery ratio", "telemetry_pdr.png", "PDR is delivered packets divided by attempted packets. The saved matrix reports 1.0 in every configuration under the stated deadline queue model."),
    ("Residual bandwidth", "telemetry_residual_bandwidth.png", "System residual bandwidth is the sum of the three RSU residuals, so its nominal maximum is 30 MHz."),
    ("Weighted system transmission rate", "telemetry_transmissions_per_second.png", "This plot shows total attempted packets divided by total active vehicle-seconds. It is an active-time-weighted system aggregate, not an unweighted arithmetic mean or a series of individual vehicle rates."),
]

NAVY = "#16324F"
BLUE = "#246A9A"
TEAL = "#2A9D8F"
PALE = "#EAF1F6"
INK = "#1F2933"
MUTED = "#52616B"
GRID = "#D5DEE5"
PAGE_W, PAGE_H = 8.27, 11.69


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_inputs() -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    summary = load_csv(SUMMARY_CSV)
    vehicles = load_csv(VEHICLE_CSV)
    config = json.loads((ROOT / "config" / "telemetry_experiments.json").read_text(encoding="utf-8"))
    expected = len(config["vehicle_counts"]) * len(config["upload_intervals_ms"]) * len(config["path_loss_exponents"])
    system_rows = [r for r in summary if r["scope"] == "system"]
    rsu_rows = [r for r in summary if r["scope"] == "rsu"]
    keys = {(r["vehicle_count"], r["upload_interval_ms"], r["path_loss_exponent"]) for r in system_rows}
    if len(summary) != 120 or len(system_rows) != expected or len(rsu_rows) != expected * 3 or len(keys) != expected:
        raise ValueError("Saved summary CSV does not match the expected 30-configuration, 120-row matrix")
    if len(vehicles) != 1110:
        raise ValueError(f"Expected 1,110 per-vehicle rows, found {len(vehicles)}")
    return summary, vehicles, config


def new_page(title: str, subtitle: str | None = None, page_no: int = 1):
    fig = plt.figure(figsize=(PAGE_W, PAGE_H), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.966), 1, 0.034, color=NAVY, transform=ax.transAxes, clip_on=False))
    ax.text(0.075, 0.93, title, fontsize=19, color=NAVY, weight="bold", va="top", transform=ax.transAxes)
    if subtitle:
        ax.text(0.075, 0.89, subtitle, fontsize=9.2, color=MUTED, va="top", transform=ax.transAxes)
    ax.plot([0.075, 0.925], [0.055, 0.055], color=GRID, lw=0.7, transform=ax.transAxes)
    ax.text(0.075, 0.032, "V2I digital twin telemetry overhead | B.Tech 5th semester", fontsize=7.5, color=MUTED, transform=ax.transAxes)
    ax.text(0.925, 0.032, str(page_no), fontsize=8, color=MUTED, ha="right", transform=ax.transAxes)
    return fig, ax


def paragraph(ax, text: str, x: float, y: float, width: float = 0.85, size: float = 10.2, color: str = INK, line_height: float = 1.4, weight=None):
    ax.text(x, y, text, transform=ax.transAxes, ha="left", va="top", color=color, fontsize=size, linespacing=line_height, wrap=True, weight=weight)


def label_value(ax, label: str, value: str, x: float, y: float, width: float = 0.84):
    ax.text(x, y, label, transform=ax.transAxes, fontsize=10, color=BLUE, weight="bold", va="top")
    paragraph(ax, value, x, y - 0.035, width=width, size=9.7, line_height=1.38)


def add_table(ax, cell_text, col_labels, bbox, col_widths=None, font_size=8.5, row_scale=1.35):
    table = ax.table(cellText=cell_text, colLabels=col_labels, cellLoc="center", colLoc="center", bbox=bbox, colWidths=col_widths)
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1, row_scale)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_linewidth(0.55)
        if row == 0:
            cell.set_facecolor(NAVY)
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor("#F5F8FA" if row % 2 == 0 else "white")
            cell.get_text().set_color(INK)
    return table


def fmt(value: str | float, digits=3):
    return f"{float(value):,.{digits}f}"


def make_report() -> Path:
    summary, vehicle_rows, config = load_inputs()
    system_rows = [r for r in summary if r["scope"] == "system"]
    rsu_rows = [r for r in summary if r["scope"] == "rsu"]
    maximum_system = max(system_rows, key=lambda r: float(r["utilization_percent"]))
    maximum_rsu = max(rsu_rows, key=lambda r: float(r["utilization_percent"]))
    utilization_values = [float(r["utilization_percent"]) for r in summary]
    pdr_values = [float(r["pdr"]) for r in summary]
    by_rate = defaultdict(list)
    for row in vehicle_rows:
        by_rate[int(row["upload_interval_ms"])].append(float(row["transmissions_per_second"]))

    REPORTS.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    figures = []

    # 1. Cover, abstract and purpose.
    fig = plt.figure(figsize=(PAGE_W, PAGE_H), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.64), 1, 0.36, color=NAVY, transform=ax.transAxes))
    ax.text(0.085, 0.91, "V2I DIGITAL TWIN", fontsize=11, color="#B9D8E8", weight="bold", transform=ax.transAxes)
    ax.text(0.085, 0.84, "Telemetry Overhead", fontsize=29, color="white", weight="bold", transform=ax.transAxes)
    ax.text(0.085, 0.785, "in Vehicular Edge Computing Networks", fontsize=15, color="white", transform=ax.transAxes)
    ax.text(0.085, 0.715, "Technical report | B.Tech 5th semester assignment", fontsize=10, color="#D6E6EF", transform=ax.transAxes)
    ax.text(0.085, 0.57, "Abstract", fontsize=15, color=NAVY, weight="bold", transform=ax.transAxes)
    paragraph(ax, "This study quantifies the channel-time and bandwidth overhead of continuously synchronizing vehicle state to roadside digital twins. A one-road SUMO scenario supplies mobility samples for five tested densities and three upload intervals. A separate log-distance V2I link model estimates theoretical Shannon capacity, and a deterministic shared-channel FCFS queue converts that capacity into packet service times and deadline outcomes. The saved experiment contains 30 configurations, 120 system and RSU summary rows, and 1,110 per-vehicle rows. In those saved results, packet delivery ratio is 1.0 throughout; maximum system-wide utilization is 5.1092%, while maximum individual-RSU utilization is 10.1023%. No system or RSU case exceeds the strict 60% threshold over the tested 5 to 100 vehicle range. These results describe the stated idealized model, not measured radio-network performance.", 0.085, 0.535, width=0.83, size=10.2, line_height=1.45)
    ax.text(0.085, 0.315, "Problem", fontsize=13, color=NAVY, weight="bold", transform=ax.transAxes)
    paragraph(ax, "Frequent Digital Twin updates consume uplink airtime that would otherwise be available for edge-task offloading. The overhead depends on update frequency, vehicle-to-RSU link quality, and the number of vehicles sharing each RSU channel.", 0.085, 0.285, width=0.82, size=9.8)
    ax.text(0.085, 0.205, "Objectives", fontsize=13, color=NAVY, weight="bold", transform=ax.transAxes)
    paragraph(ax, "Measure modeled telemetry utilization, packet delivery ratio, residual bandwidth, and per-vehicle upload rate; compare density, update interval, and path-loss exponent; and identify whether any tested case exceeds 60% utilization.", 0.085, 0.175, width=0.82, size=9.8)
    ax.text(0.085, 0.045, "Report generated from the saved workspace CSVs and plots. The report build does not launch SUMO or modify experiment results.", fontsize=7.7, color=MUTED, transform=ax.transAxes)
    figures.append(fig)

    # 2. Scenario and experimental design.
    fig, ax = new_page("Scenario and experimental design", "SUMO mobility foundation and the evaluated configuration matrix", 2)
    label_value(ax, "Road and RSUs", "One straight, single-lane, 1,000 m road. Three RSUs are fixed at x = 250 m, 500 m, and 750 m. Vehicles are assigned to their nearest RSU using one-dimensional along-road distance.", 0.075, 0.835)
    label_value(ax, "SUMO observation", "The saved configuration specifies a 0.05 s simulation step and a 120 s observation duration. Vehicles start from rest at evenly spaced positions across the first 75% of the road; the same mobility trace is reused across upload intervals and exponents.", 0.075, 0.68)
    label_value(ax, "Factor matrix", "Five densities x three upload intervals x two path-loss exponents give 30 configurations. n = 3.0 is the 15-case core matrix; n = 3.5 is the 15-case sensitivity run.", 0.075, 0.52)
    rows = [["Vehicles", "Intervals", "Exponents", "Configurations"], ["5, 10, 20, 50, 100", "50, 100, 500 ms", "3.0, 3.5", "30 total"], ["Summary rows", "Per-vehicle rows", "RSU channels", "Observation"], ["120 (30 system + 90 RSU)", "1,110", "3 x 10 MHz", "120 s; 50 ms step"]]
    add_table(ax, rows[1:], rows[0], [0.075, 0.27, 0.85, 0.16], font_size=7.7)
    paragraph(ax, "The summary CSV reports each configuration at system scope and once per RSU. The per-vehicle CSV stores attempted packets, observed active time, and transmissions per second.", 0.075, 0.23, width=0.84, size=9.1)
    paragraph(ax, "Source configuration: config/telemetry_experiments.json. Road and RSU positions come from config/smoke_test.json.", 0.075, 0.145, width=0.84, size=8.5, color=MUTED)
    figures.append(fig)

    # 3. Channel model.
    fig, ax = new_page("V2I channel model", "Unit-explicit link budget and theoretical capacity", 3)
    params = [
        ["Carrier frequency", "5.9 GHz"], ["Channel bandwidth", "10 MHz = 10,000,000 Hz"],
        ["Vehicle transmit power", "20 dBm"], ["Noise PSD", "-174 dBm/Hz"],
        ["Receiver noise figure", "9 dB"], ["Reference distance", "1 m"],
        ["Reference path loss", "Friis free-space at 1 m = 47.8648 dB"],
        ["Path-loss exponent", "n = 3.0 or 3.5"], ["Telemetry payload", "32 bytes = 256 bits"],
    ]
    add_table(ax, params, ["Parameter", "Configured value"], [0.075, 0.57, 0.85, 0.29], col_widths=[0.4, 0.6], font_size=8.6)
    equations = [
        ("Wavelength", "lambda = c / f; c = 299,792,458 m/s and f = 5.9 x 10^9 Hz."),
        ("Reference loss", "PL(d0) = 20 log10(4 pi d0 / lambda) = 47.8648 dB for d0 = 1 m."),
        ("Log-distance path loss", "PL(d) = PL(d0) + 10 n log10(d / d0), for d >= d0."),
        ("Received power", "Pr (dBm) = Pt (dBm) - PL(d) (dB)."),
        ("Receiver noise", "N (dBm) = N0 (dBm/Hz) + 10 log10(B in Hz) + NF (dB) = -95 dBm."),
        ("SNR", "SNRdB = Pr - N; SNRlinear = 10^(SNRdB / 10)."),
        ("Shannon capacity", "C = B log2(1 + SNRlinear), in bits/s. This is theoretical link capacity, not measured throughput."),
        ("Payload service time", "t = 32 x 8 / C seconds, an ideal payload serialization time used by the queue model."),
    ]
    y = 0.535
    for head, body in equations:
        ax.text(0.085, y, head, fontsize=9.3, weight="bold", color=BLUE, transform=ax.transAxes, va="top")
        ax.text(0.31, y, body, fontsize=8.6, color=INK, transform=ax.transAxes, va="top", wrap=True)
        y -= 0.052
    paragraph(ax, "The one-dimensional model uses absolute x-distance to the nearest RSU. Integrated simulation distances below 1 m are clamped to the reference distance. The channel calculation omits fading, shadowing, antenna gains, interference, collisions, PHY errors, and retransmissions.", 0.075, 0.11, width=0.84, size=8.6, color=MUTED)
    figures.append(fig)

    # 4. Packet format and queue access.
    fig, ax = new_page("Telemetry packets and shared-channel access", "How periodic state updates become packet outcomes", 4)
    fields = [["Field", "Bytes", "Contents represented"], ["Position", "12", "Vehicle location"], ["Speed", "4", "Vehicle speed"], ["Heading", "4", "Travel direction"], ["Acceleration", "4", "Longitudinal acceleration"], ["Timestamp", "8", "State sample time"], ["Total", "32", "256 payload bits"]]
    add_table(ax, fields[1:], fields[0], [0.075, 0.55, 0.85, 0.30], col_widths=[0.28, 0.12, 0.60], font_size=8.7)
    steps = [
        "Each RSU has one independent, shared 10 MHz uplink channel.",
        "A perfectly coordinated, non-preemptive FCFS queue serves one packet at a time per RSU. Independent RSUs can serve packets concurrently.",
        "Packets with equal generation times are ordered by vehicle ID, then packet ID. Arrival concurrency therefore creates a queue, not a collision.",
        "The service duration is 256 payload bits divided by that vehicle-to-RSU Shannon capacity. No protocol headers or MAC overhead are added.",
        "A packet is delivered only if serialization completes by its next-upload deadline (generation time + interval) and by the 120 s observation end. A packet missing either boundary is dropped before transmission and consumes no airtime.",
        "Queue deadline misses and observation-end misses are the only modeled packet-loss causes. There are no collision, interference, random PHY-error, or retransmission processes.",
    ]
    y = 0.50
    for i, item in enumerate(steps, 1):
        ax.add_patch(plt.Circle((0.095, y - 0.009), 0.014, color=TEAL, transform=ax.transAxes))
        ax.text(0.095, y - 0.009, str(i), color="white", fontsize=7.5, weight="bold", ha="center", va="center", transform=ax.transAxes)
        ax.text(0.13, y + 0.006, item, fontsize=8.3, color=INK, transform=ax.transAxes, va="top", wrap=True)
        y -= 0.066 if i != 5 else 0.08
    paragraph(ax, "This deterministic queue is a capacity/deadline abstraction, not a full physical-layer or standards-compliant MAC simulation.", 0.075, 0.095, width=0.85, size=8.4, color=MUTED)
    figures.append(fig)

    # 5. Metric definitions and numerical summary.
    fig, ax = new_page("Metrics and saved results", "CSV-derived summary at the highest tested density", 5)
    metrics = [
        ["Metric", "Definition and scope"],
        ["RSU utilization (%)", "100 x delivered serialization seconds at that RSU / 120 s."],
        ["System utilization (%)", "100 x sum of delivered airtime over 3 RSUs / (3 x 120 s); the average RSU busy fraction."],
        ["PDR", "Delivered / attempted packets, by RSU or system. CSV range: 1.000 to 1.000."],
        ["RSU residual (MHz)", "10 x (1 - RSU utilization / 100)."],
        ["System residual (MHz)", "Sum of the three RSU residuals, at most 30 MHz."],
        ["Individual rate\n(1/s)", "One vehicle: attempts / active seconds.\nCSV: transmissions_per_second."],
        ["Weighted system\nrate (1/s)", "Total attempts / total active vehicle-seconds.\nCSV: mean_transmissions_per_vehicle_second."]
    ]
    add_table(ax, metrics[1:], metrics[0], [0.075, 0.58, 0.85, 0.27], col_widths=[0.27, 0.73], font_size=7.9)
    selected = [r for r in system_rows if int(r["vehicle_count"]) == 100]
    selected.sort(key=lambda r: (int(r["upload_interval_ms"]), float(r["path_loss_exponent"])))
    matrix = [[r["upload_interval_ms"], r["path_loss_exponent"], fmt(r["utilization_percent"], 4), fmt(r["pdr"], 3), fmt(r["residual_bandwidth_mhz"], 4), fmt(r["mean_transmissions_per_vehicle_second"], 3)] for r in selected]
    ax.text(0.075, 0.49, f"Maximum system utilization: {fmt(maximum_system['utilization_percent'], 4)}% | Maximum individual RSU: {fmt(maximum_rsu['utilization_percent'], 4)}% ({maximum_rsu['rsu_id']}). Both at 100 vehicles, 50 ms, n=3.5.", fontsize=8.7, color=INK, transform=ax.transAxes, va="top", wrap=True)
    ax.text(0.075, 0.425, "System-wide results at 100 vehicles", fontsize=10.3, color=NAVY, weight="bold", transform=ax.transAxes)
    add_table(ax, matrix, ["Interval\n(ms)", "n", "Util.\n(%)", "PDR", "Residual\n(MHz)", "Weighted\nsystem rate\n(1/s)"], [0.075, 0.09, 0.85, 0.31], col_widths=[0.14, 0.09, 0.18, 0.13, 0.22, 0.24], font_size=6.9, row_scale=1.0)
    figures.append(fig)

    # 6-9. Existing plots, one per page.
    for plot_index, (title, filename, caption) in enumerate(PLOT_FILES, start=1):
        page = plot_index + 6
        fig, ax = new_page(title, "Saved plot from results/; plotted values are drawn from the saved summary CSV.", page)
        image = plt.imread(ROOT / "results" / filename)
        ax.imshow(image, extent=(0.075, 0.925, 0.23, 0.80), aspect="auto", transform=ax.transAxes)
        caption_title = (
            "Weighted system rate by density, upload interval, and path-loss exponent"
            if plot_index == 4
            else f"{title} by vehicle density, upload interval, and path-loss exponent"
        )
        ax.text(0.075, 0.185, f"Figure {plot_index}. {caption_title}.", fontsize=9.0, color=NAVY, weight="bold", transform=ax.transAxes, va="top", wrap=True)
        ax.text(0.075, 0.135, caption, fontsize=9.0, color=INK, transform=ax.transAxes, va="top", wrap=True)
        figures.append(fig)

    # 10. Threshold and interpretation.
    fig, ax = new_page("Saturation threshold and interpretation", "Strict 60% comparison, evaluated separately by interval, exponent, and scope", 10)
    threshold_rows = []
    for interval in config["upload_intervals_ms"]:
        for exponent in config["path_loss_exponents"]:
            subset_sys = [r for r in system_rows if int(r["upload_interval_ms"]) == interval and float(r["path_loss_exponent"]) == exponent]
            subset_rsu = [r for r in rsu_rows if int(r["upload_interval_ms"]) == interval and float(r["path_loss_exponent"]) == exponent]
            threshold_rows.append([str(interval), f"{float(exponent):g}", "System-wide", "No crossing", fmt(max(float(r["utilization_percent"]) for r in subset_sys), 4) + "%"])
            for rsu_id in ("rsu1", "rsu2", "rsu3"):
                vals = [r for r in subset_rsu if r["rsu_id"] == rsu_id]
                threshold_rows.append([str(interval), f"{float(exponent):g}", rsu_id, "No crossing", fmt(max(float(r["utilization_percent"]) for r in vals), 4) + "%"])
    add_table(ax, threshold_rows, ["Interval\n(ms)", "n", "Scope", "First >60%", "Max observed"], [0.075, 0.38, 0.85, 0.43], col_widths=[0.14, 0.09, 0.26, 0.25, 0.26], font_size=7.2, row_scale=1.0)
    paragraph(ax, "Threshold definition: for each interval, exponent, and scope, select the first tested vehicle count with utilization strictly greater than 60.0%; equality does not qualify. No saved value equals or exceeds 60.0%. All 24 scope-specific groups return no threshold over 5, 10, 20, 50, and 100 vehicles.", 0.075, 0.34, width=0.84, size=9.0)
    paragraph(ax, "Telemetry overhead rises with more frequent uploads and larger path-loss exponent, but remains below the threshold in this tested scenario. These results do not establish a general network saturation density.", 0.075, 0.18, width=0.84, size=8.9)
    figures.append(fig)

    # 11. Limitations and conclusion.
    fig, ax = new_page("Limitations and conclusion", "Results are model outputs, not field measurements", 11)
    ax.text(0.075, 0.84, "Model limitations", fontsize=12, color=NAVY, weight="bold", transform=ax.transAxes)
    paragraph(ax, "The link model is one-dimensional and line-of-sight, with large-scale log-distance loss and no shadowing, fading, antenna gains, interference, or blockage. Shannon capacity is an ideal upper bound used to form payload service time; it is not observed throughput. The access model assumes perfect centralized FCFS scheduling and independent RSU channels. It has no collisions, random PHY errors, retransmissions, protocol headers, or realistic MAC control behavior. Consequently, PDR = 1.0 means that every packet met the modeled deadline in the saved scenario; it is not a real-world delivery guarantee. Residual bandwidth is an idle-time share translated to MHz and does not simulate task-offload traffic or its own scheduling.", 0.075, 0.80, width=0.84, size=9.3)
    ax.text(0.075, 0.575, "Conclusion", fontsize=12, color=NAVY, weight="bold", transform=ax.transAxes)
    paragraph(ax, f"For the saved 30-case experiment, utilization remains below 60% at both scopes for every tested density from 5 through 100 vehicles. The observed maxima are {fmt(maximum_system['utilization_percent'], 4)}% system-wide and {fmt(maximum_rsu['utilization_percent'], 4)}% for RSU {maximum_rsu['rsu_id']}; both occur at 100 vehicles, 50 ms uploads, and n=3.5. PDR remains 1.0 across the saved summary. No saturation threshold was observed within the tested matrix. The matrix and plots were already present in the workspace; creating this report did not rerun SUMO.", 0.075, 0.535, width=0.84, size=9.3)
    ax.text(0.075, 0.365, "Per-vehicle transmission rates in the saved CSV", fontsize=10.3, color=NAVY, weight="bold", transform=ax.transAxes)
    rate_rows = []
    for interval in (50, 100, 500):
        values = by_rate[interval]
        rate_rows.append([f"{interval} ms", f"{1000 / interval:.0f} /s", f"{min(values):.4f} to {max(values):.4f} /s"])
    add_table(ax, rate_rows, ["Upload interval", "Nominal rate", "Observed CSV range"], [0.075, 0.19, 0.85, 0.145], col_widths=[0.30, 0.27, 0.43], font_size=8.2)
    ax.text(0.075, 0.155, "Ranges include all 1,110 vehicle rows. The denominator is each vehicle's observed active time, so finite trace boundaries produce small differences from nominal rates.", fontsize=8.3, color=MUTED, transform=ax.transAxes, va="top", wrap=True)
    figures.append(fig)

    # 12. Reproduction commands and artifact locations.
    fig, ax = new_page("Reproduction commands and files", "PowerShell commands from the project root", 12)
    ax.text(0.075, 0.84, "Run and validation commands", fontsize=12, color=NAVY, weight="bold", transform=ax.transAxes)
    commands = [
        ("Full SUMO experiment (rewrites results)", r".\.venv\Scripts\python.exe src\telemetry_experiment.py"),
        ("Plots only from saved summary CSV", r".\.venv\Scripts\python.exe src\telemetry_experiment.py --plots-only"),
        ("Full pytest suite", r".\.venv\Scripts\python.exe -m pytest -q"),
        ("SUMO + TraCI smoke test", r".\.venv\Scripts\python.exe src\sumo_smoke_test.py"),
        ("Channel example at n = 3.0", r".\.venv\Scripts\python.exe src\channel_simulation.py --path-loss-exponent 3.0"),
    ]
    y = 0.78
    for label, command in commands:
        ax.text(0.085, y, label, fontsize=8.4, color=BLUE, weight="bold", transform=ax.transAxes, va="top")
        ax.text(0.085, y - 0.023, command, fontsize=8.1, color=INK, family="DejaVu Sans Mono", transform=ax.transAxes, va="top")
        y -= 0.10
    ax.text(0.085, 0.23, r"Channel at n = 3.5: .\.venv\Scripts\python.exe src\channel_simulation.py --path-loss-exponent 3.5", fontsize=8.0, color=INK, family="DejaVu Sans Mono", transform=ax.transAxes, va="top")
    ax.text(0.075, 0.15, "Saved inputs: results/telemetry_summary.csv, results/telemetry_per_vehicle.csv, and the four results/*.png plots. Experiment parameters are in config/telemetry_experiments.json.", fontsize=8.8, color=MUTED, transform=ax.transAxes, va="top", wrap=True)
    figures.append(fig)

    # Save each exact layout to the PDF and to QA PNGs for visual inspection.
    with PdfPages(PDF_PATH) as pdf:
        for page_number, fig in enumerate(figures, 1):
            fig.savefig(QA / f"page-{page_number}.png", dpi=120, facecolor="white")
            pdf.savefig(fig, facecolor="white")
            plt.close(fig)
    return PDF_PATH


if __name__ == "__main__":
    path = make_report()
    print(f"Created {path}")
