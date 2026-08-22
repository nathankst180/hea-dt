"""
data/generate_fault_data.py

Synthetic fault-injection dataset generator for training M4 (Electrical &
Powertrain RUL) and M5 (Structural Health Monitoring). Separate from
scripts/generate_sample_telemetry.py, which produces one plausible demo
flight for the replay UI — this generates MANY labeled flights across 6
scenario types and 3 severity levels, specifically for supervised training.

DESIGN — why this isn't just "make up some numbers":

  Every simulated flight is fed through the REAL M2 (SensorValidator) and
  M3 (TwinStateEstimator) pipeline as it's generated — the exact same code
  path a live MAVLink connection or a replayed upload would run through.
  M4/M5's features are then extracted from M2/M3's OUTPUT (StateVector,
  TwinParams), not raw telemetry directly. This matters for two reasons:

  1. It matches the architecture: M4/M5 are documented as consuming
     (state, twin), not raw telemetry — training on anything else would
     be a train/inference mismatch.
  2. It's a genuine end-to-end exercise of the real pipeline: M3's EKF
     really does have to track a synthetically-aging battery's resistance
     from noisy voltage/current readings, and M2 really does have to
     isolate the injected sensor glitches, before M4/M5 ever see a
     feature. A dataset that instead hand-computed "ground truth" features
     directly would silently validate nothing about M2/M3.

SCENARIOS (6, matching the project's fault-injection plan):

  healthy         — normal variation only, no injected fault.
  aging_battery   — internal resistance grows linearly over the flight
                     (R(t) = R_nominal + rate*t, same V = V_oc - I*R model
                     M3's EKF assumes — see modules/m3_twin_state/
                     estimator.py). Ground-truth time-to-failure is
                     computed analytically from the known rate.
  weak_motor      — one randomly-chosen motor undershoots RPM for its
                     commanded PWM AND draws extra current for the same
                     RPM (an efficiency loss), rather than the fleet's
                     normal PWM<->RPM<->current signature.
  prop_imbalance  — a rotor-synchronous oscillation is added to accel/gyro,
                     correlated with one rotor's RPM. See the important
                     sample-rate caveat in modules/m5_structural_shm/
                     features.py's docstring: this is generated at a
                     higher rate specifically so the injected oscillation
                     is resolvable, and the frequency is a stylized proxy
                     for a real (much higher) rotor harmonic, not a
                     physically accurate one.
  high_wind       — an environmental condition, not a component fault: a
                     legitimate NEGATIVE example for M4/M5 (more attitude
                     correction, airspeed/groundspeed mismatch) that must
                     not be confused with a real fault — included so the
                     false-alarm-rate metric means something.
  sensor_glitch   — isolated, single-row, reverting bad readings, injected
                     via modules/m1_telemetry/mock_source.py's
                     apply_sensor_glitch() — the EXACT function
                     MockTelemetrySource uses, so this is the same fault
                     shape M2 was built to catch. Since every row here
                     runs through the real M2 before M4/M5 see it, M2
                     should clean these before they ever reach a feature —
                     this scenario is as much a regression check on M2 as
                     it is training data.

Run:
    python data/generate_fault_data.py
    python data/generate_fault_data.py --flights-per-severity 10 --duration 60

Output (under data/, all Parquet):
    battery_dataset.parquet     one row per timestep — M4's battery RUL regressor
    motor_dataset.parquet       one row per (timestep, motor) — M4's weak-motor classifier
    structural_dataset.parquet  one row per timestep — M5's imbalance classifier
Each has a `split` column ("train"/"val"/"test"), assigned per FLIGHT (not
per row, which would leak sequential correlation across splits) with every
severity level represented in all three splits.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# This script lives in data/, one level below the project root, and isn't
# run as a package (`python data/generate_fault_data.py`, not `-m`) — so
# the root (where config/, contracts/, modules/ live) isn't on sys.path by
# default. Same fix as the project's conftest.py uses for pytest.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.loader import load_config
from contracts.schemas import RawState
from modules.m1_telemetry.mock_source import apply_sensor_glitch
from modules.m2_validation import SensorValidator
from modules.m3_twin_state import TwinStateEstimator
from modules.m4_electrical_rul.features import extract_battery_features, extract_motor_features
from modules.m5_structural_shm.features import extract_spectral_features, per_rotor_correlation

logging.disable(logging.INFO)  # keep dataset generation output readable — see print()s in main()

SCENARIOS = ["healthy", "aging_battery", "weak_motor", "high_wind", "prop_imbalance", "sensor_glitch"]
SEVERITIES = ["mild", "moderate", "severe"]
N_MOTORS = 4

# Matches modules/m1_telemetry/mock_source.py and modules/m3_twin_state/estimator.py exactly.
V_EMPTY, V_FULL = 13.2, 16.8
NOMINAL_R_OHM = 0.05
HOVER_RPM = 5000.0

R_FAIL_OHM = 0.15
"""Internal resistance threshold beyond which the battery is considered
critically degraded — 3x the nominal 0.05 ohm starting point. Used both
to compute ground-truth time-to-failure for aging_battery, and to derive
battery_soh_pct deterministically at inference (see predictor.py)."""

NO_FAILURE_TTF_S = 3600.0
"""Ground-truth RUL label for flights with no battery degradation trend
(right-censored: "not expected to fail within an hour")."""

# aging_battery: internal-resistance growth rate, ohm/s, by severity.
AGING_BATTERY_RATE = {"mild": 0.0003, "moderate": 0.0012, "severe": 0.0050}

# weak_motor: fractional efficiency loss (RPM undershoot / current excess), by severity.
WEAK_MOTOR_LOSS = {"mild": 0.15, "moderate": 0.35, "severe": 0.60}

# prop_imbalance: (accel amplitude m/s^2, oscillation freq Hz), by severity.
# See module docstring: freq is a STYLIZED proxy, not a real rotor harmonic.
PROP_IMBALANCE_PARAMS = {
    "mild": (0.30, 1.0),
    "moderate": (0.80, 1.5),
    "severe": (1.80, 2.2),
}

# high_wind: wind speed, m/s, by severity.
HIGH_WIND_SPEED = {"mild": 4.0, "moderate": 8.0, "severe": 14.0}

# sensor_glitch: per-row glitch probability, by severity.
GLITCH_PROB = {"mild": 0.02, "moderate": 0.06, "severe": 0.15}

SEVERITY_LEVEL = {"n/a": 0, "mild": 1, "moderate": 2, "severe": 3}


def _open_circuit_voltage(remaining_pct: float) -> float:
    pct = max(0.0, min(100.0, remaining_pct))
    return V_EMPTY + (V_FULL - V_EMPTY) * (pct / 100.0)


def _baseline_raw_state(t: float, rng: random.Random, remaining_pct: float, alt_m: float, r_batt: float) -> RawState:
    """A nominal, healthy reading at simulated time t. Near-hover with
    small random jitter — flight-path realism isn't the point here (see
    scripts/generate_sample_telemetry.py for that); an isolatable, clean
    fault SIGNAL is."""
    current_a = 9.0 + rng.uniform(-1.5, 1.5)
    v_oc = _open_circuit_voltage(remaining_pct)
    battery_v = v_oc - current_a * r_batt + rng.uniform(-0.03, 0.03)

    rpm = [HOVER_RPM + rng.uniform(-80, 80) for _ in range(N_MOTORS)]
    pwm = [1500.0 + rng.uniform(-15, 15) for _ in range(N_MOTORS)]

    accel_xyz = (rng.uniform(-0.15, 0.15), rng.uniform(-0.15, 0.15), 9.81 + rng.uniform(-0.05, 0.05))
    gyro_xyz = (rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01), rng.uniform(-0.01, 0.01))

    ground_speed = rng.uniform(0.0, 1.5)
    heading = rng.uniform(-math.pi, math.pi)

    return RawState(
        timestamp=t,
        battery_voltage_v=battery_v,
        battery_current_a=current_a,
        battery_remaining_pct=remaining_pct,
        battery_temp_c=25.0 + rng.uniform(-1.5, 3.0),
        motor_pwm=pwm,
        motor_rpm=rpm,
        esc_current_a=[current_a / N_MOTORS] * N_MOTORS,
        esc_temp_c=[30.0 + rng.uniform(-3.0, 8.0) for _ in range(N_MOTORS)],
        accel_xyz=accel_xyz,
        gyro_xyz=gyro_xyz,
        attitude_rpy=(rng.uniform(-0.03, 0.03), rng.uniform(-0.03, 0.03), heading),
        position_lla=(47.3977, 8.5456, alt_m),
        velocity_ned=(ground_speed * math.cos(heading), ground_speed * math.sin(heading), 0.0),
        airspeed_ms=ground_speed + rng.uniform(-0.3, 0.3),
        groundspeed_ms=ground_speed,
        wind_ned=(0.5, 0.2, 0.0),
        gps_fix_type=3,
        gps_satellites_visible=rng.randint(10, 14),
        armed=True,
        flight_mode="AUTO",
    )


def simulate_flight(
    scenario: str, severity: str, seed: int, duration_s: float, rate_hz: float
) -> tuple[list[RawState], dict]:
    """Generate one flight's raw telemetry sequence with `scenario`'s fault
    injected at `severity`. Returns (rows, ground_truth) where
    ground_truth carries whatever this scenario needs downstream to label
    each row (rotor/motor index, resistance growth rate, ...)."""
    rng = random.Random(seed)
    dt = 1.0 / rate_hz
    n_steps = int(duration_s / dt) + 1
    alt_m = 60.0 + rng.uniform(-10, 10)

    gt: dict = {"scenario": scenario, "severity": severity}
    rows: list[RawState] = []

    remaining_pct = 90.0
    r_batt = NOMINAL_R_OHM
    aging_rate = AGING_BATTERY_RATE.get(severity, 0.0) if scenario == "aging_battery" else 0.0
    gt["aging_rate_ohm_per_s"] = aging_rate

    weak_motor_idx = rng.randrange(N_MOTORS) if scenario == "weak_motor" else None
    weak_loss = WEAK_MOTOR_LOSS.get(severity, 0.0) if scenario == "weak_motor" else 0.0
    gt["weak_motor_idx"] = weak_motor_idx

    imbalance_rotor_idx = rng.randrange(N_MOTORS) if scenario == "prop_imbalance" else None
    imbalance_amp, imbalance_freq = PROP_IMBALANCE_PARAMS.get(severity, (0.0, 0.0)) if scenario == "prop_imbalance" else (0.0, 0.0)
    imbalance_phase = rng.uniform(0, 2 * math.pi)
    gt["imbalance_rotor_idx"] = imbalance_rotor_idx
    gt["imbalance_amp"] = imbalance_amp
    gt["imbalance_freq_hz"] = imbalance_freq

    wind_speed = HIGH_WIND_SPEED.get(severity, 0.0) if scenario == "high_wind" else 0.0
    wind_dir = rng.uniform(0, 2 * math.pi)

    glitch_prob = GLITCH_PROB.get(severity, 0.0) if scenario == "sensor_glitch" else 0.0
    glitch_field = rng.choice(["gps", "battery_voltage", "imu"])

    for i in range(n_steps):
        t = i * dt
        remaining_pct = max(0.0, remaining_pct - 0.01 * dt)

        if scenario == "aging_battery":
            r_batt = NOMINAL_R_OHM + aging_rate * t

        raw = _baseline_raw_state(t, rng, remaining_pct, alt_m, r_batt)

        if scenario == "weak_motor" and weak_motor_idx is not None:
            i_w = weak_motor_idx
            rpm = list(raw.motor_rpm)
            esc_current = list(raw.esc_current_a)
            rpm[i_w] *= 1.0 - weak_loss
            esc_current[i_w] *= 1.0 + weak_loss
            raw.motor_rpm = rpm
            raw.esc_current_a = esc_current

        if scenario == "prop_imbalance" and imbalance_rotor_idx is not None:
            osc = imbalance_amp * math.sin(2 * math.pi * imbalance_freq * t + imbalance_phase)
            ax, ay, az = raw.accel_xyz
            raw.accel_xyz = (ax + osc, ay + 0.4 * osc, az)
            gx, gy, gz = raw.gyro_xyz
            raw.gyro_xyz = (gx, gy, gz + 0.05 * osc)
            rpm = list(raw.motor_rpm)
            rpm[imbalance_rotor_idx] += 60.0 * math.sin(2 * math.pi * imbalance_freq * t + imbalance_phase)
            raw.motor_rpm = rpm

        if scenario == "high_wind":
            # Wind adds RANDOM (not clean-sinusoidal) attitude jitter — spectrally
            # distinct from prop_imbalance's injected sinusoid, so a spectral
            # classifier has a real signal to tell them apart on, not just amplitude.
            roll, pitch, yaw = raw.attitude_rpy
            jitter = wind_speed / 14.0  # normalize severity into a 0-1ish jitter scale
            raw.attitude_rpy = (roll + rng.uniform(-jitter, jitter) * 0.15, pitch + rng.uniform(-jitter, jitter) * 0.15, yaw)
            headwind = wind_speed * math.cos(wind_dir)
            raw.airspeed_ms = (raw.airspeed_ms or 0.0) + abs(headwind) + rng.uniform(-0.5, 0.5)

        if scenario == "sensor_glitch" and rng.random() < glitch_prob:
            battery_v, gps_fix, gps_sats, accel_xyz = apply_sensor_glitch(
                glitch_field, raw.battery_voltage_v, raw.gps_fix_type, raw.gps_satellites_visible, raw.accel_xyz, rng
            )
            raw.battery_voltage_v = battery_v
            raw.gps_fix_type = gps_fix
            raw.gps_satellites_visible = gps_sats
            raw.accel_xyz = accel_xyz

        rows.append(raw)

    return rows, gt


def process_flight(
    rows: list[RawState], gt: dict, split: str, flight_id: str, config
) -> tuple[list[dict], list[dict], list[dict]]:
    """Run one flight's raw rows through the REAL M2+M3 pipeline, extract
    M4/M5 features from their output, and label every row. Returns
    (battery_rows, motor_rows, structural_rows)."""
    validator = SensorValidator(config)
    estimator = TwinStateEstimator(config)

    scenario, severity = gt["scenario"], gt["severity"]
    severity_level = SEVERITY_LEVEL[severity if scenario != "healthy" else "n/a"]

    accel_window: list[tuple[float, float, float]] = []
    gyro_window: list[tuple[float, float, float]] = []
    ts_window: list[float] = []
    rpm_window: list[list[float]] = []
    window_size = 32

    battery_rows, motor_rows, structural_rows = [], [], []

    for raw in rows:
        state = validator.validate(raw)
        twin = estimator.estimate(state)

        # ---- battery (M4) ----
        aging_rate = gt["aging_rate_ohm_per_s"]
        if aging_rate > 0:
            ttf = max(0.0, min(NO_FAILURE_TTF_S, (R_FAIL_OHM - (NOMINAL_R_OHM + aging_rate * raw.timestamp)) / aging_rate))
        else:
            ttf = NO_FAILURE_TTF_S
        soh_true = 100.0 * max(0.0, min(1.0, (R_FAIL_OHM - twin.battery_internal_resistance_ohm) / (R_FAIL_OHM - NOMINAL_R_OHM)))

        battery_row = extract_battery_features(state, twin)
        battery_row.update(
            scenario=scenario, severity=severity, severity_level=severity_level, split=split, flight_id=flight_id,
            battery_rul_s_true=ttf, battery_soh_pct_true=soh_true,
        )
        battery_rows.append(battery_row)

        # ---- motor (M4) ----
        weak_idx = gt["weak_motor_idx"]
        for m_idx, motor_feat in enumerate(extract_motor_features(state, twin)):
            motor_row = dict(motor_feat)
            motor_row.update(
                scenario=scenario, severity=severity, severity_level=severity_level, split=split, flight_id=flight_id,
                motor_id=m_idx, motor_weak_true=int(weak_idx is not None and m_idx == weak_idx),
            )
            motor_rows.append(motor_row)

        # ---- structural (M5) ----
        accel_window.append(raw.accel_xyz)
        gyro_window.append(raw.gyro_xyz)
        ts_window.append(raw.timestamp)
        rpm_window.append(list(raw.motor_rpm))
        if len(accel_window) > window_size:
            accel_window.pop(0)
            gyro_window.pop(0)
            ts_window.pop(0)
            rpm_window.pop(0)

        spectral = extract_spectral_features(accel_window, gyro_window, ts_window)
        rotor_scores = per_rotor_correlation(accel_window, rpm_window)
        rotor_idx = gt["imbalance_rotor_idx"]
        structural_row = dict(spectral)
        for m in range(N_MOTORS):
            structural_row[f"rotor_corr_{m}"] = rotor_scores[m] if m < len(rotor_scores) else 0.0
        structural_row.update(
            scenario=scenario, severity=severity, severity_level=severity_level, split=split, flight_id=flight_id,
            imbalance_true=int(scenario == "prop_imbalance"),
            prop_damage_true=(severity_level / 3.0 if scenario == "prop_imbalance" else 0.0),
            imbalance_rotor_true=(rotor_idx if rotor_idx is not None else -1),
        )
        structural_rows.append(structural_row)

    return battery_rows, motor_rows, structural_rows


def assign_splits(n: int, seed: int) -> list[str]:
    """One split label per flight index, guaranteeing >=1 flight in val
    and test (when n allows) so every severity level is represented in
    all three splits, not just train."""
    if n <= 0:
        return []
    rng = random.Random(seed)
    order = list(range(n))
    rng.shuffle(order)

    n_val = max(1, round(n * 0.15)) if n >= 3 else (1 if n >= 2 else 0)
    n_test = max(1, round(n * 0.15)) if n >= 3 else 0
    n_train = n - n_val - n_test

    labels = ["train"] * n_train + ["val"] * n_val + ["test"] * n_test
    result = [""] * n
    for position, flight_index in enumerate(order):
        result[flight_index] = labels[position]
    return result


def generate_dataset(
    flights_per_severity: int, duration_s: float, rate_hz: float, config, seed: int = 0
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_battery, all_motor, all_structural = [], [], []
    t_start = time.time()

    for scenario in SCENARIOS:
        # healthy has only one "severity" bucket (n/a), but we still want
        # roughly as many healthy rows as any single fault scenario — an
        # important negative class, especially for M5's binary classifier
        # — so it gets flights_per_severity * len(SEVERITIES) flights too,
        # not 1/3 as many.
        severities = ["n/a"] if scenario == "healthy" else SEVERITIES
        flights_per_bucket = flights_per_severity * (len(SEVERITIES) if scenario == "healthy" else 1)
        n_flights_total = flights_per_bucket * len(severities)
        splits = assign_splits(n_flights_total, seed=hash((scenario, "split")) % (2**31))

        flight_i = 0
        for severity in severities:
            for _ in range(flights_per_bucket):
                flight_seed = seed + hash((scenario, severity, flight_i)) % (2**20)
                sim_severity = severity if severity != "n/a" else "mild"  # unused for healthy, kept for dict lookups
                rows, gt = simulate_flight(scenario, sim_severity, flight_seed, duration_s, rate_hz)
                if scenario == "healthy":
                    gt["severity"] = "n/a"
                flight_id = f"{scenario}_{severity}_{flight_i:03d}"
                split = splits[flight_i]

                b, m, s = process_flight(rows, gt, split, flight_id, config)
                all_battery.extend(b)
                all_motor.extend(m)
                all_structural.extend(s)
                flight_i += 1

        print(f"  {scenario:16s} {n_flights_total:3d} flights  ({time.time() - t_start:5.1f}s elapsed)")

    return pd.DataFrame(all_battery), pd.DataFrame(all_motor), pd.DataFrame(all_structural)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic fault-injection training dataset.")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--flights-per-severity", type=int, default=6)
    parser.add_argument("--duration", type=float, default=40.0, help="Seconds of simulated flight per sample.")
    parser.add_argument("--rate-hz", type=float, default=8.0, help="Sample rate — higher than the live loop rate so M5's injected oscillation is resolvable (see module docstring).")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    config = load_config("config/config.yaml")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating dataset: {args.flights_per_severity} flights/severity, "
          f"{args.duration:.0f}s @ {args.rate_hz}Hz ({int(args.duration * args.rate_hz) + 1} rows/flight)")

    battery_df, motor_df, structural_df = generate_dataset(
        args.flights_per_severity, args.duration, args.rate_hz, config, seed=args.seed
    )

    battery_path = output_dir / "battery_dataset.parquet"
    motor_path = output_dir / "motor_dataset.parquet"
    structural_path = output_dir / "structural_dataset.parquet"
    battery_df.to_parquet(battery_path, index=False)
    motor_df.to_parquet(motor_path, index=False)
    structural_df.to_parquet(structural_path, index=False)

    print(f"\nWrote {len(battery_df):6d} rows -> {battery_path}")
    print(f"Wrote {len(motor_df):6d} rows -> {motor_path}")
    print(f"Wrote {len(structural_df):6d} rows -> {structural_path}")
    print("\nRows per scenario (battery table):")
    print(battery_df.groupby(["scenario", "severity"]).size().to_string())
    print("\nSplit sizes (battery table):")
    print(battery_df["split"].value_counts().to_string())


if __name__ == "__main__":
    main()
