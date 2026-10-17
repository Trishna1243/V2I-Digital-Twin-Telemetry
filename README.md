# CC: V2I digital twin synchronization overhead

This workspace contains the SUMO + TraCI scenario and a separate, testable
large-scale V2I channel model. The model estimates average link power, SNR, and
theoretical Shannon capacity for a 32-byte state update payload.

## Run the smoke test

From PowerShell in the project directory:

```powershell
.\.venv\Scripts\python.exe src\sumo_smoke_test.py
```

Edit `config/smoke_test.json` to change the number of vehicles, simulation
steps, road length, or the three RSU positions. The script builds a one-road
network and route in a temporary directory, starts SUMO through TraCI, and
reads each vehicle's time, position, speed, and road ID. It prints the
observed final state and leaves generated files out of the workspace.

## Watch a SUMO-GUI demonstration

Launch the separate graphical demo from PowerShell:

```powershell
.\.venv\Scripts\python.exe src\sumo_gui_demo.py
```

It writes its network, route, RSU marker, and `demo.sumocfg` files to the
persistent `sumo_demo/` directory, then starts SUMO-GUI playback at a 50 ms
delay per 50 ms simulation step. The demo keeps the configured road, vehicle
type, and RSU positions, and uses a shorter step for smooth playback. The
default simulation duration is 300
seconds. SUMO-GUI stays open when the simulation ends; close the window when
you are finished. Choose a different
vehicle count or output location with, for example:

```powershell
.\.venv\Scripts\python.exe src\sumo_gui_demo.py --vehicles 10
```

The demo uses a straight, one-lane road and the three RSU positions from
`config/smoke_test.json`. The RSUs are labelled SUMO POI visual markers (for
example, `RSU1_250m`); they
are not simulated transmitters and do not change the V2I channel model or
experiment results. This script is separate from both the temporary-file
smoke test and the telemetry experiment.

## V2I channel model

Run the integrated SUMO and channel calculation from PowerShell:

```powershell
.\.venv\Scripts\python.exe src\channel_simulation.py --path-loss-exponent 3.0
.\.venv\Scripts\python.exe src\channel_simulation.py --path-loss-exponent 3.5
```

At each SUMO sample, the script takes the vehicle's along-road position and
selects the nearest of the three configured RSUs. This starter road is
one-dimensional, so distance is the absolute difference between the SUMO x
coordinate and the RSU x coordinate. It reports the link budget per vehicle
sample. The 32-byte payload is a configured telemetry size; this initial
model converts it to bits and reports its ideal serialization time at the
Shannon capacity. This is a lower-bound theoretical duration, not measured
packet airtime.

The reference path loss uses the Friis free-space expression at 5.9 GHz and
the 1 m reference distance:

```text
lambda = c / f
PL(d0) = 20 log10(4 pi d0 / lambda) = approximately 47.86 dB
PL(d)  = PL(d0) + 10 n log10(d / d0), for d >= d0
Pr     = Pt - PL(d)
N      = -174 dBm/Hz + 10 log10(B in Hz) + NF
SNRdB  = Pr - N
SNR    = 10 ** (SNRdB / 10)
C      = B * log2(1 + SNR), in bits/second
t32    = (32 bytes * 8 bits/byte) / C, ideal seconds at Shannon capacity
```

The default parameters are 20 dBm transmit power, 10 MHz bandwidth, 9 dB
receiver noise figure, and 32 bytes per telemetry update. The noise density
is -174 dBm/Hz. Path-loss exponent is configurable as 3.0 or 3.5 in the
integrated command. Distances below 1 m in the integrated simulation are
clamped to the 1 m reference distance; the standalone path-loss function
rejects distances below its reference distance. Positions and RSUs use a
straight-road, line-of-sight distance approximation. The calculation omits
shadowing, fast fading, antenna gains, interference, contention, packet
collisions, packet error rate, and retransmissions. Shannon capacity is a
theoretical upper bound for the modeled SNR and bandwidth. The payload
serialization time is correspondingly ideal and excludes protocol headers,
guard intervals, scheduling, and retransmissions; neither metric represents
application throughput or a packet-delivery guarantee.

## Digital-twin telemetry experiments

Run the complete density × interval matrix and exponent sensitivity analysis:

```powershell
.\.venv\Scripts\python.exe src\telemetry_experiment.py
```

The matrix in `config/telemetry_experiments.json` uses 5, 10, 20, 50, and 100
vehicles; upload periods of 50, 100, and 500 ms; and both path-loss exponents
3.0 and 3.5. The core results are the 15 configurations with exponent 3.0;
the other 15 are the exponent sensitivity analysis. SUMO runs with a 50 ms
step. For each density, it places the vehicles from rest at evenly spaced
positions over the first 75% of the 1 km road so every configured vehicle is
present in the scenario. The same mobility trace is reused across upload
periods and exponents.

The telemetry packet contains 12 bytes of position, 4 bytes of speed, 4 bytes
of heading, 4 bytes of acceleration, and 8 bytes of timestamp. Its 256 payload
bits are sent at a periodic rate of `1 / interval`; the per-vehicle rates are
20, 10, and 2 attempted updates per second for the three intervals. Channel
parameters (5.9 GHz carrier, 10 MHz bandwidth, 20 dBm transmit power,
-174 dBm/Hz noise density, 9 dB noise figure, and 1 m reference distance)
are in the same JSON file and can be changed without editing the equations.

### Shared-channel access and packet delivery

Shannon capacity alone does not predict packet delivery. This experiment uses
an explicit simplified access model, implemented in `src/telemetry_model.py`:

1. Each RSU has one independent shared 10 MHz uplink channel.
2. Packets for one RSU are served one at a time by a perfectly coordinated,
   non-preemptive first-come, first-served queue. Packets with the same
   generation time are ordered by vehicle ID, then packet ID. Different RSUs
   transmit independently and may serve packets at the same time.
3. Each packet's serialization time is its 256 payload bits divided by that
   vehicle-to-RSU link's Shannon capacity. This models capacity-limited service
   without counting the same RSU capacity separately for every vehicle.
4. A packet succeeds if its serialization completes by the next upload
   deadline (`generation time + upload interval`) and within the SUMO
   observation window. If queueing pushes completion past either boundary, it
   is dropped before transmission. Thus queue deadline misses are the only
   modeled packet-loss cause. There are no random collisions or PHY errors.

This assumes a perfect central scheduler. It is a transparent queueing
abstraction for capacity and deadline effects, not a full physical-layer,
collision, or standards-compliant MAC simulation. In particular, it does not
represent protocol headers or retransmissions.

### Bandwidth metrics

Metrics are calculated per RSU and across the three independent RSU channels:

```text
RSU utilization (%) = 100 * delivered telemetry serialization time / observation time
system utilization (%) = 100 * sum(RSU delivered serialization time) / (3 * observation time)
PDR = delivered telemetry packets / attempted telemetry packets
RSU residual bandwidth = 10 MHz * (1 - RSU utilization / 100)
system residual bandwidth = sum(RSU residual bandwidth), at most 30 MHz
individual vehicle rate (`transmissions_per_second`) = that vehicle's attempted packets / its observed active seconds
weighted system aggregate (`mean_transmissions_per_vehicle_second`) = total attempted packets / total active vehicle-seconds
```

The summary CSV's `mean_transmissions_per_vehicle_second` field is an
active-time-weighted system aggregate: each individual vehicle rate is weighted
by that vehicle's active seconds. It is not an unweighted arithmetic mean and
does not contain separate per-vehicle rates. Those are in
`telemetry_per_vehicle.csv` under `transmissions_per_second`.

Per-RSU utilization and residual bandwidth account for one shared channel
once. The system utilization is the average busy fraction across three
independent channels, and system residual bandwidth is their sum, not a single
10 MHz channel counted once per vehicle. Utilization counts successfully
transmitted payload airtime; packets dropped before transmission consume no
airtime. Residual bandwidth treats the idle time fraction as available to
task offloading. It is a time-share estimate and does not model task traffic,
scheduling overhead, or interference between RSUs.

### Results and interpretation

The experiment writes these files under `results/`:

- `telemetry_summary.csv`: one row per RSU and one system row for each of the
  30 density, interval, and exponent combinations (120 rows total).
- `telemetry_per_vehicle.csv`: per-vehicle attempted packet counts, active
  time, and transmissions per second (1110 rows in the supplied matrix).
- `telemetry_utilization.png`, `telemetry_pdr.png`,
  `telemetry_residual_bandwidth.png`, and
  `telemetry_transmissions_per_second.png`: labelled plots of the four metrics.

For the supplied matrix, no tested density crossed 60% utilization, either
system-wide or on an individual RSU. The largest observed system utilization
was 5.11%, and the largest per-RSU utilization was 10.10%; both occurred for
100 vehicles, 50 ms uploads, and exponent 3.5. PDR was 1.0 in all 30
configurations under this scheduler and deadline rule. These are results of
the stated simplified model and scenario, not a general network threshold.
The experiment prints this 60% check when run. To regenerate plots from saved
CSV without launching SUMO:

```powershell
.\.venv\Scripts\python.exe src\telemetry_experiment.py --plots-only
```

### Tests

Run the channel unit tests without launching SUMO:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_channel_model.py -v
```

Run both the channel tests and existing SUMO smoke check:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe src\sumo_smoke_test.py
```

Run telemetry access, metric aggregation, and channel tests together:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Project folders

- `config/`: smoke-test parameters
- `src/`: runnable project code
- `src/channel_model.py`: pure channel equations and nearest-RSU helper
- `src/channel_simulation.py`: SUMO integration and per-sample link budgets
- `src/telemetry_model.py`: RSU queue access, packet outcomes, and metrics
- `src/telemetry_experiment.py`: experiment matrix, CSV output, and plots
- `sumo/`: SUMO-related documentation and future scenario files
- `tests/`: test documentation and future automated tests
- `results/`: generated results (ignored by Git)
