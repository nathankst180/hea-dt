# HEA-DT — Health- and Energy-Aware Adaptive Digital Twin

A drone digital twin that predicts battery/motor/structural health and
energy needs, then closes the loop by sending adaptive mission commands
back to a simulated drone's autopilot in real time. Simulation-only
(PX4/ArduPilot SITL + Gazebo) — no real hardware required.

This repo is currently at **Phase 2: predictive intelligence**, plus an
early replay API + dashboard. **Layer A** — M1 (Telemetry & MAVLink
Gateway), M2 (Data Validation & Sensor-Fault Isolation), M3 (Digital-Twin
State Estimation) — and **Layer B** — M4 (Electrical & Powertrain RUL), M5
(Structural Health Monitoring), M6 (Energy Modeling & Optimization), M7
(Uncertainty Quantification) — are both fully implemented and trained on a
6-scenario synthetic fault dataset (`data/generate_fault_data.py`). M8
through M12 are still Phase 0 stubs: each logs its own call and returns a
well-typed placeholder object, so the full 12-module pipeline runs
end-to-end before the rest of the modules' real logic is written.

A FastAPI backend (`api/`) exposes that replay pipeline over HTTP/
WebSocket, and a separate React frontend (`../frontend/`) renders it as a
live 3D dashboard — upload a telemetry file, watch the digital twin,
predictions, closed-loop decision, and decision log update cycle by
cycle. See [Replay API & Frontend](#replay-api--frontend-dashboard) below,
or the repo-root `README.md` for the fastest "run both" path.

## Project structure

```
hea-dt/
  contracts/          # Shared typed dataclasses passed between every module (schemas.py).
                       # Read this file first — it's the contract every module is built against.
  modules/
    m1_telemetry/      # [done] MAVLinkGateway, MockTelemetrySource, ReplayTelemetrySource (base.py + 3 sources)
    m2_validation/      # [done] Sensor-fault isolation, data cleaning (validator.py)
    m3_twin_state/       # [done] EKF-based physical parameter estimation + deviation δ(t) (estimator.py)
    m4_electrical_rul/    # [done] Battery RUL (LightGBM) + per-motor weak-motor classifier (LightGBM)
    m5_structural_shm/     # [done] FFT/PSD spectral features + Random Forest imbalance classifier
    m6_energy/               # [done] Physics-based power model + scipy/grid speed-altitude optimizer
    m7_uncertainty/            # [done] MAPIE conformal wrappers + deviation-gated confidence fusion
    m8_fusion/                   # Health + energy -> feasibility forward-simulation
    m9_decision/                   # NOMINAL / REOPTIMIZE / REDUCE_SCOPE / ABORT FSM
    m10_safety/                      # Safety envelope + reversibility/confidence gating
    m11_actuation/                     # Sanctioned command -> real MAVLink write-back
    m12_logging/                         # Hash-chained, explainable decision log
  orchestrator/        # main.py — the loop that calls M1 through M12 in sequence each cycle,
                        # returning a CycleResult (every module's output, not just the LogRecord)
  api/                  # [done] FastAPI replay backend — upload, replay/start|stop, /ws/state, /logs
  scripts/                # generate_sample_telemetry.py (demo flight CSV) + demo_layer_b.py (M4-M7 scenario demo)
  config/                  # config.yaml (all tunables) + loader.py (typed Config object)
  data/                     # generate_fault_data.py (6-scenario training dataset generator) + its *.parquet output,
                             # missions/, uploads/ (replay files), decision_log.sqlite
  training/models/            # Saved model artifacts: M4/M5's *.joblib, M7's MAPIE conformal wrappers
  results/                      # m4_metrics.json, m5_metrics.json, m7_metrics.json — reported numbers for the paper
  tests/                          # pytest suite
  requirements.txt
```

The frontend dashboard lives one level up, as a sibling directory:
`../frontend/` (its own `package.json` — see `frontend/README.md`).

M13 (Command-Channel Trust & Security) and M14 (Fleet Federated Learning)
are extensions and don't have module directories yet — they're built only
if time allows, per the project plan.

## Shared data contracts

Every module's input and output is one of the dataclasses in
[`contracts/schemas.py`](contracts/schemas.py):

```
M1 -> RawState -> M2 -> StateVector -> M3 -> TwinParams
                                              |
                        +---------------------+---------------------+
                        v                     v                     v
                 M4 HealthElectrical   M5 HealthStructural   M6 EnergyPlan
                        +---------------------+---------------------+
                                              v
                                     M7 -> Confidence
                                              v
                                     M8 -> Feasibility
                                              v
                                     M9 -> Decision
                                              v
                                     M10 -> Command (or withheld, pending a human)
                                              v
                                     M11 -> Ack
                                              v
                                     M12 -> LogRecord
```

Every field has a docstring explaining what it means and why it matters —
read `contracts/schemas.py` if you're unsure what a field like `deviation`
or `decision_confidence` represents physically.

## Layer A: sensing (M1 → M2 → M3)

- **M1** (`modules/m1_telemetry/`) has two interchangeable implementations
  of the shared `TelemetrySource` interface (`base.py`):
  - `MAVLinkGateway` (`gateway.py`) — a real, bidirectional pymavlink
    connection to PX4/ArduPilot SITL. It subscribes to `BATTERY_STATUS`,
    `RAW_IMU`/`SCALED_IMU`, `ATTITUDE`, `GPS_RAW_INT`, `SYS_STATUS`/
    `HEARTBEAT`, and (if streamed) `SERVO_OUTPUT_RAW`/`ESC_STATUS`,
    `VFR_HUD`, `GLOBAL_POSITION_INT`, and `WIND` — converting each
    message's native units (mG, degE7, cA, ...) to the SI units
    `RawState` expects. It also implements the **write side**:
    `send_test_command(speed_ms)` sends `MAV_CMD_DO_CHANGE_SPEED` and
    waits for `COMMAND_ACK`, proving the channel is bidirectional (M11,
    in Phase 4, builds the full sanctioned-command pipeline on the same
    pattern). `connect()` raises `ConnectionError` — not a hang — if no
    heartbeat arrives within `heartbeat_timeout_s`.
  - `MockTelemetrySource` (`mock_source.py`) — generates telemetry from a
    battery model that shares M3's open-circuit-voltage assumptions, so a
    `--mock` run is physically coherent rather than pure noise.
    `MockFaultConfig` (wired to `config.yaml`'s `mock:` section) can
    inject a declining battery, extra vibration noise, or a transient
    single-cycle sensor glitch (GPS fix loss, a battery voltage spike, or
    an IMU spike) — see below.
- **M2** (`modules/m2_validation/validator.py`, class `SensorValidator`)
  cleans each `RawState` into a `StateVector`: hard range checks, gap
  imputation from the last known-good value, a GPS-fix-quality check, a
  GPS-vs-IMU-integrated-speed cross-consistency check, and an
  `IsolationForest` anomaly detector. Its central job — and the one most
  worth reading the docstrings for — is telling a **sensor fault** (an
  isolated, single-cycle, physically-implausible or discontinuous
  reading) apart from a **real component problem** (a persistent,
  gradually-developing trend, which is deliberately left unflagged so
  M4/M5 still see it).
- **M3** (`modules/m3_twin_state/estimator.py`, class `TwinStateEstimator`)
  runs a 3-state Extended Kalman Filter (mass, battery internal
  resistance, a global thrust-efficiency factor) against a lightweight
  physics model, and reports `deviation` — the normalized measurement
  residual. A **sudden** contradiction (e.g. voltage collapsing far below
  what the converged resistance estimate predicts) spikes `deviation`; a
  **gradual** real trend (a battery genuinely wearing out over many
  cycles) instead gets absorbed into the resistance estimate itself —
  watch `R_batt` climb cycle over cycle in the flow printout below, which
  *is* the twin self-calibrating to a real drifting parameter, exactly as
  designed.

Demonstrate either fault path directly:

```bash
python -c "
from config.loader import load_config
from modules.m1_telemetry import MockTelemetrySource, MockFaultConfig
from modules.m2_validation import SensorValidator
from modules.m3_twin_state import TwinStateEstimator

config = load_config('config/config.yaml')
faults = MockFaultConfig(battery_decline_v_per_s=0.5)  # or glitch_prob=1.0, glitch_field='gps'
source = MockTelemetrySource(seed=7, faults=faults)
validator = SensorValidator(config)
estimator = TwinStateEstimator(config)
for _ in range(6):
    raw = source.get_raw_state()
    state = validator.validate(raw)
    twin = estimator.estimate(state)
    print(twin.deviation, twin.battery_internal_resistance_ohm, state.sensor_fault_flags)
"
```

## Layer B: predictive intelligence (M4 → M7)

### The synthetic fault dataset

`data/generate_fault_data.py` generates labeled training data for 6
scenario types — `healthy`, `aging_battery`, `weak_motor`, `high_wind`,
`prop_imbalance`, `sensor_glitch` — at 3 severity levels each
(`mild`/`moderate`/`severe`; `healthy` has one "n/a" bucket sized to match).
The key design decision: **every simulated flight is run through the REAL
M2 (`SensorValidator`) and M3 (`TwinStateEstimator`) before any M4/M5
feature is extracted** — not hand-computed "ground truth" features. This
means training data is in the exact representation M4/M5 see at inference
(matching the architecture, where they consume `(state, twin)`, not raw
telemetry), and it's a genuine end-to-end exercise of M2/M3 against
synthetic faults: M3's EKF really has to track an aging battery's
resistance from noisy readings, and M2 really has to isolate injected
sensor glitches, for the resulting features to look right.

Regenerate it (takes ~2-3 minutes at the defaults: 18 flights/scenario,
40s @ 8Hz each ≈ 5,760 rows/scenario, ~35k total rows, split 67/17/17 by
FLIGHT — not by row, which would leak sequential correlation — with every
severity level represented in all three splits):

```bash
python data/generate_fault_data.py
python data/generate_fault_data.py --flights-per-severity 10 --duration 60   # more data, slower
```

Writes `data/battery_dataset.parquet`, `data/motor_dataset.parquet`
(4 rows per timestep, one per motor), and `data/structural_dataset.parquet`.

**Sample-rate honesty note**, worth reading before trusting M5's numbers
too far: real propeller-imbalance detection needs IMU sampling well above
the rotor's harmonic frequency (~83 Hz for a 5000 RPM motor), but
`RawState` is one reading per pipeline cycle (1-10 Hz here), not a
buffered high-rate IMU stream. `prop_imbalance` is generated at a higher
rate (8 Hz) specifically so an injected oscillation is resolvable at all,
and that oscillation's frequency (~1-2 Hz) is a **stylized proxy** for the
real, much higher rotor harmonic — the mechanism (spectral peak detection,
harmonic-band energy, motor-command correlation) is genuine and
demonstrable at the rates this prototype actually runs at, but the
absolute frequency isn't physically realistic. See
`modules/m5_structural_shm/features.py`'s docstring, and put this in the
paper's limitations section.

### Training

```bash
python -m modules.m4_electrical_rul.train      # LightGBM battery-RUL regressor + per-motor weak-motor classifier
python -m modules.m5_structural_shm.train      # Random Forest imbalance classifier + damage-severity regressor
python -m modules.m7_uncertainty.calibration   # MAPIE conformal wrappers around M4's/M5's trained models (run after both, above)
```

Each writes its model artifact(s) to `training/models/` (`config.paths.models_dir`)
and its metrics to `results/*.json`. Representative numbers from the
default dataset (yours will vary slightly — this is a small synthetic set,
not a claim of state-of-the-art performance):

| | metric | value |
|---|---|---|
| M4 battery RUL | MAE, aging_battery rows only (test) | ~120-420s (noisy — only 3 held-out flights per severity; more flights would tighten this) |
| M4 motor-weak classifier | accuracy / F1 (test) | ~1.00 / ~1.00 — the injected fault is very separable once M3's per-motor efficiency estimate is a feature |
| M5 imbalance classifier | accuracy / false-alarm rate (test) | ~0.997 / ~0.0 (including on the `high_wind`/`sensor_glitch` "confusable" subset specifically) |
| M7 battery RUL conformal | empirical coverage @ 90% target | ~0.87 |
| M7 imbalance conformal | empirical coverage @ 90% target | ~0.95 |

**Why M4's motor classifier and M5's imbalance classifier score so high**:
not data leakage (splits are by flight, and test flights are never seen
during training or calibration) — the engineered features are simply
highly informative by construction, especially M3's own per-motor
thrust-efficiency estimate, which is itself already a strong physics-based
signal for "this motor is behaving differently." A perfect classifier
score here says more about feature engineering quality than about the
underlying detection problem being trivial in general.

### M6 — no training needed (it's physics, not ML)

`modules/m6_energy/optimizer.py` implements a power model — actuator-disk
hover power, translational-lift induced-power falloff, cubic parasitic
drag, altitude-dependent air density, battery-resistance loss derating —
built from textbook relationships with reasonable-but-unmeasured
constants (`config.m6`), since there's no real aerodynamic test data for
this airframe. Read the module docstring before citing any of its output
numbers: it documents exactly which relationships are textbook-accurate
(e.g. hover power genuinely *increasing* with altitude — thinner air needs
more induced power, the reason helicopters have service ceilings) versus
simplified (e.g. wind treated as a worst-case headwind regardless of
actual heading). `scipy.optimize.minimize_scalar` searches speed for each
of a small altitude grid, pulling `mass`/`motor_thrust_efficiency`/
`battery_internal_resistance_ohm` from **M3's live TwinParams**, not
datasheet defaults — the stated differentiator versus prior work.

### M7's confidence fusion — the "uncertainty-gated autonomy" formula

```
decision_confidence = min(conf_battery, conf_structural)      # base: the LEAST confident prediction, not an average
                     * exp(-twin.deviation / deviation_scale)  # deviation_factor: multiplicative, not additive
                     * clip(1 - 0.15*len(sensor_fault_flags), 0.3, 1.0)  # data-quality factor
```

`conf_battery`/`conf_structural` come from MAPIE `SplitConformalRegressor`/
`SplitConformalClassifier` (v1 API, `prefit=True` around the already-fitted
M4/M5 models, calibrated on the `val` split, coverage checked on `test` —
see `modules/m7_uncertainty/calibration.py`). The formula is multiplicative
specifically so a highly confident model prediction still gets pulled down
proportionally the moment `twin.deviation` rises — see
`modules/m7_uncertainty/quantifier.py`'s docstring for the full reasoning,
and `tests/test_m7_uncertainty.py` for the test that checks exactly this.

### See it respond to different faults

```bash
python scripts/demo_layer_b.py
```

Generates one flight per scenario (reusing `generate_fault_data.py`'s own
`simulate_flight()` — not mock random data) and prints the
`RawState -> StateVector -> TwinParams -> HealthElectrical/HealthStructural/
EnergyPlan -> Confidence` flow at the start/middle/end of each. Look for:
`aging_battery`'s `battery_rul_s` collapsing and `R_batt` visibly climbing;
`weak_motor`'s `motor_health` isolating exactly the injured motor to ~0
while its siblings stay at 1.0; `prop_imbalance`'s `imbalance_flag`
flipping true partway through (once the spectral window fills) without M2
ever misflagging it as a sensor fault; `high_wind`/`sensor_glitch` staying
healthy-looking throughout, since neither is a real component problem.

## Replay API & Frontend Dashboard

`ReplayTelemetrySource` (`modules/m1_telemetry/replay_source.py`) is the
third `TelemetrySource` implementation: it loads an uploaded CSV/Parquet
telemetry file and replays it through `orchestrator.run_cycle()` — the
exact same function the CLI and a live MAVLink connection use — in
original timestamp order, paced by a speed multiplier
(`load_telemetry_file()` / column format documented in that file's
docstring; `scripts/generate_sample_telemetry.py` writes an example).

`api/server.py` (FastAPI) wraps that in a small HTTP+WebSocket API:

| Endpoint | Purpose |
|---|---|
| `POST /upload` | Upload a `.csv`/`.parquet` file, get back a `session_id` + row count/duration. |
| `POST /replay/start` | Start replaying a session at a given `speed_multiplier` (runs in a background thread). |
| `POST /replay/stop` | Stop an in-progress replay. |
| `GET /replay/status` | Poll `IDLE/RUNNING/STOPPED/FINISHED/ERROR` + cycles produced so far. |
| `GET /logs` | Pull every `LogRecord` produced so far for a session (M12's decision log). |
| `WS /ws/state?session_id=...` | Streams one JSON message per pipeline cycle — every module's output (`api/serialization.py::cycle_result_to_dict`) — backfilling anything already produced if you connect mid-replay. |

Run it (from `hea-dt/`):

```bash
uvicorn api.server:app --reload --port 8000
```

Generate a demo flight and try it without the frontend:

```bash
python scripts/generate_sample_telemetry.py          # writes data/sample_telemetry.csv
curl -F "file=@data/sample_telemetry.csv" http://127.0.0.1:8000/upload
curl -X POST http://127.0.0.1:8000/replay/start -H "Content-Type: application/json" \
     -d '{"session_id": "<paste session_id here>", "speed_multiplier": 10}'
curl "http://127.0.0.1:8000/replay/status?session_id=<...>"
```

For the actual dashboard, run this alongside `../frontend/` — see the
repo-root `README.md` for the two-terminal "run both" instructions.

## Configuration

All tunables — simulator connection, loop rates, per-module model choice,
safety thresholds, file paths — live in [`config/config.yaml`](config/config.yaml).
Nothing should be hardcoded in module code; if you need a new tunable, add
it to `config.yaml` and to the matching dataclass in `config/loader.py`.

Load it in code with:

```python
from config.loader import load_config
config = load_config("config/config.yaml")
config.thresholds.min_altitude_m  # -> 10.0
```

## Running the orchestrator

Install dependencies (Python 3.10+):

```bash
pip install -r requirements.txt
```

Run the full pipeline in **mock mode** — fake/random telemetry instead of
a real simulator connection, so you can test the wiring without Gazebo/SITL
running:

```bash
python -m orchestrator.main --mock
```

This runs 5 cycles by default, logging each module's call through its own
`hea_dt.m<N>_<name>` logger, and prints the full
`RawState -> StateVector -> TwinParams -> HealthElectrical/HealthStructural/
EnergyPlan -> Confidence` flow for every cycle (M4/M5/M7 fall back to
well-typed "fully healthy"/"fully confident" stub output with a one-time
warning if you haven't trained them yet — see
[Layer B](#layer-b-predictive-intelligence-m4--m7) above). Useful flags:

```bash
python -m orchestrator.main --mock --cycles 20          # run more cycles
python -m orchestrator.main --mock --no-flow             # suppress the flow printout, keep only the logs
python -m orchestrator.main --mock --config path/to.yaml  # use a different config
python -m orchestrator.main                                 # live mode: connects M1 to config.sim.connection (needs a running SITL)
```

To see Layer A notice a problem live rather than just in the tests, edit
`config.yaml`'s `mock:` section (e.g. set `battery_decline_v_per_s: 0.5`
or `glitch_prob: 0.3`) before running with `--mock`. To see Layer B
respond to different injected faults specifically, use
`python scripts/demo_layer_b.py` instead (see above) — mock mode's random
data doesn't carry the structured fault signatures M4/M5 were trained on.

## Running the tests

```bash
pytest
```

`tests/test_contracts.py` checks every dataclass instantiates correctly.
`tests/test_m1_telemetry.py`, `test_m2_validation.py`, `test_m3_twin_state.py`
cover Layer A specifically — including the sensor-vs-component-fault
distinction and the deviation metric spiking on contradictory data.
`tests/test_m1_replay.py` covers `ReplayTelemetrySource`'s file parsing and
playback pacing. `tests/test_m4_electrical_rul.py`, `test_m5_structural_shm.py`,
`test_m6_energy.py`, `test_m7_uncertainty.py` cover Layer B — using the
actually-trained models (skipped, not failed, if you haven't run the
training scripts yet) for M4/M5/M7, and physics-only checks for M6 (e.g.
that a heavier/more-degraded aircraft shifts the optimizer's speed
suggestion, verified empirically before the test was written — see that
file's module docstring). `tests/test_orchestrator.py` runs a full
mock-mode cycle end-to-end and checks the resulting `CycleResult`/
`LogRecord` (including that the hash chain links correctly across cycles,
and that Layer B's predictions are well-formed). `tests/test_api.py`
drives the FastAPI app with `TestClient` end-to-end: upload -> replay/start
-> WebSocket stream -> status -> logs, including a late-subscriber
backfill test and a mid-replay stop test.

## Build order

Per the project plan, modules get filled in roughly in this order:

1. **Phase 0** — skeleton, contracts, config, stubbed orchestrator. Done.
2. **Phase 1** — M1-M3: real telemetry ingest, validation, twin state. Done.
3. **Phase 1.5** — replay telemetry source, FastAPI replay API, and the
   React dashboard (`../frontend/`) — upload a flight, watch Layer A's
   real output live, get the panel/timeline scaffolding ready for
   M4-M12's output as those modules land. Done.
4. **Phase 2 (this)** — M4-M7: fault-injection dataset generation
   (`data/generate_fault_data.py`), electrical RUL + structural health
   prediction, energy optimization, and MAPIE-conformal uncertainty
   quantification. Done. The frontend's Panel 2 (Predictions &
   Optimization) now renders real M4-M7 output; Panel 3 (Closed Loop)
   still shows M9's stub decision (always `NOMINAL`) until Phase 4.
5. **Phase 3 (next)** — M8: health-energy fusion + feasibility
   forward-simulation, consuming M4-M7's now-real output into one
   mission-feasibility verdict.
6. **Phase 4** — M9-M11: decision FSM, safety arbiter, real MAVLink
   actuation. **This is the core contribution — the closed loop.**
7. **Phase 5** — M12-M13: SQLite-backed logging, command-channel security.
8. **Phase 6** — M14 + evaluation sweeps. Live telemetry mode (the
   disabled "Live" tab in the frontend) gets built once there's a running
   SITL to point M1's `MAVLinkGateway` at from the API too.

Each module also needs a `mock` path (like M1/M11 already have) so it can
be developed and tested without a live simulator running.
