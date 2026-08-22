"""
scripts/generate_sample_telemetry.py

Generates a small, realistic-looking sample telemetry CSV so the replay
API and frontend can be built and demoed right now, without waiting for a
real SITL flight log or for Phase 2's real fault-injection dataset
generator (training/generate_dataset.py, built later — that one produces
labeled fault data for training M4/M5/M6; this one just needs to look
like a plausible flight for ReplayTelemetrySource to replay).

The flight profile is a simple takeoff -> one circular cruise loop ->
landing, with a battery model that reuses the same open-circuit-voltage
assumption as modules/m1_telemetry/mock_source.py and modules/
m3_twin_state/estimator.py, so the replayed flight is physically coherent
with what the twin expects (a flat/converged deviation, a smoothly
draining battery) rather than looking obviously synthetic once replayed
through the real pipeline.

Usage:
    python scripts/generate_sample_telemetry.py
    python scripts/generate_sample_telemetry.py --output data/my_flight.csv --duration 90 --seed 7
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import pandas as pd

# Same convention modules/m1_telemetry/replay_source.py expects.
_V_EMPTY, _V_FULL = 13.2, 16.8
_NOMINAL_R_OHM = 0.05
_HOVER_RPM = 5000.0
_N_MOTORS = 4

_LAT0, _LON0 = 47.3977, 8.5456
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(_LAT0))


def _phase(t: float, takeoff_s: float, cruise_s: float, land_s: float) -> tuple[str, float]:
    """Return (phase name, progress 0-1 within that phase) for time t."""
    if t < takeoff_s:
        return "takeoff", t / takeoff_s
    if t < takeoff_s + cruise_s:
        return "cruise", (t - takeoff_s) / cruise_s
    if t < takeoff_s + cruise_s + land_s:
        return "land", (t - takeoff_s - cruise_s) / land_s
    return "idle", 1.0


def generate_rows(
    duration_s: float = 120.0,
    rate_hz: float = 1.0,
    cruise_alt_m: float = 80.0,
    cruise_radius_m: float = 60.0,
    seed: int = 42,
) -> list[dict]:
    rng = random.Random(seed)

    takeoff_s = min(10.0, duration_s * 0.1)
    land_s = min(10.0, duration_s * 0.1)
    cruise_s = max(duration_s - takeoff_s - land_s, 1.0)
    cruise_period_s = cruise_s  # one full loop over the whole cruise phase
    angular_speed = 2 * math.pi / cruise_period_s

    dt = 1.0 / rate_hz
    n_steps = int(duration_s / dt) + 1

    remaining_pct = 95.0
    rows: list[dict] = []

    for i in range(n_steps):
        t = i * dt
        phase, progress = _phase(t, takeoff_s, cruise_s, land_s)

        # --- altitude & horizontal position ---
        if phase == "takeoff":
            alt_m = cruise_alt_m * progress
            angle = 0.0
            radius = 0.0
            yaw = 0.0
            vx_ms, vy_ms, vz_ms = 0.0, 0.0, -cruise_alt_m / takeoff_s  # NED: climbing = negative down
        elif phase == "cruise":
            alt_m = cruise_alt_m + rng.uniform(-0.5, 0.5)
            angle = angular_speed * (t - takeoff_s)
            radius = cruise_radius_m
            yaw = angle + math.pi / 2  # heading tangent to the circle
            tangential_speed = cruise_radius_m * angular_speed
            vx_ms = -tangential_speed * math.sin(angle)
            vy_ms = tangential_speed * math.cos(angle)
            vz_ms = 0.0
        elif phase == "land":
            alt_m = cruise_alt_m * (1.0 - progress)
            angle = angular_speed * cruise_s  # hold ending position
            radius = cruise_radius_m * (1.0 - progress)
            yaw = angle + math.pi / 2
            vx_ms, vy_ms = 0.0, 0.0
            vz_ms = cruise_alt_m / land_s
        else:  # idle, landed
            alt_m = 0.0
            angle = angular_speed * cruise_s
            radius = 0.0
            yaw = angle + math.pi / 2
            vx_ms = vy_ms = vz_ms = 0.0

        north_m = radius * math.cos(angle)
        east_m = radius * math.sin(angle)
        lat = _LAT0 + north_m / _M_PER_DEG_LAT
        lon = _LON0 + east_m / _M_PER_DEG_LON

        # --- attitude: bank into turns during cruise, level otherwise ---
        if phase == "cruise":
            roll = math.atan((cruise_radius_m * angular_speed**2) / 9.81) * 0.6
            pitch = -0.05
        else:
            roll = 0.0
            pitch = -0.05 if phase in ("takeoff", "land") else 0.0

        # --- motors: near-hover, a bit higher during takeoff/climb ---
        base_rpm = _HOVER_RPM * (1.08 if phase == "takeoff" else 1.0)
        motor_rpm = [base_rpm + rng.uniform(-100, 100) for _ in range(_N_MOTORS)]
        motor_pwm = [1500.0 + (r - _HOVER_RPM) * 0.05 + rng.uniform(-10, 10) for r in motor_rpm]

        # --- battery: simple discharge draining faster during takeoff ---
        current_a = {"takeoff": 16.0, "cruise": 10.0, "land": 8.0, "idle": 3.0}[phase] + rng.uniform(-1.0, 1.0)
        discharge_pct_per_s = 0.03 if phase == "takeoff" else 0.015
        remaining_pct = max(0.0, remaining_pct - discharge_pct_per_s * dt)
        v_oc = _V_EMPTY + (_V_FULL - _V_EMPTY) * (remaining_pct / 100.0)
        battery_v = v_oc - current_a * _NOMINAL_R_OHM + rng.uniform(-0.05, 0.05)

        accel_x = vy_ms * angular_speed * 0.0 + rng.uniform(-0.2, 0.2)  # small residual noise
        accel_y = rng.uniform(-0.2, 0.2)
        accel_z = 9.81 + rng.uniform(-0.15, 0.15)
        gyro_z = angular_speed if phase == "cruise" else 0.0

        row = {
            "timestamp": round(t, 3),
            "battery_voltage_v": round(battery_v, 3),
            "battery_current_a": round(current_a, 2),
            "battery_remaining_pct": round(remaining_pct, 2),
            "battery_temp_c": round(25.0 + rng.uniform(-1, 3), 2),
            "airspeed_ms": round(math.hypot(vx_ms, vy_ms) + rng.uniform(-0.3, 0.3), 2),
            "groundspeed_ms": round(math.hypot(vx_ms, vy_ms), 2),
            "gps_fix_type": 3,
            "gps_satellites_visible": rng.randint(10, 14),
            "armed": True,
            "flight_mode": {"takeoff": "AUTO", "cruise": "AUTO", "land": "AUTO", "idle": "AUTO"}[phase],
            "accel_xyz_x": round(accel_x, 3),
            "accel_xyz_y": round(accel_y, 3),
            "accel_xyz_z": round(accel_z, 3),
            "gyro_xyz_x": 0.0,
            "gyro_xyz_y": 0.0,
            "gyro_xyz_z": round(gyro_z, 4),
            "attitude_rpy_x": round(roll, 4),
            "attitude_rpy_y": round(pitch, 4),
            "attitude_rpy_z": round(((yaw + math.pi) % (2 * math.pi)) - math.pi, 4),
            "velocity_ned_x": round(vx_ms, 3),
            "velocity_ned_y": round(vy_ms, 3),
            "velocity_ned_z": round(vz_ms, 3),
            "wind_ned_x": 0.5,
            "wind_ned_y": 0.2,
            "wind_ned_z": 0.0,
            "position_lat": round(lat, 7),
            "position_lon": round(lon, 7),
            "position_alt_m": round(alt_m, 2),
        }
        for m in range(_N_MOTORS):
            row[f"motor_pwm_{m + 1}"] = round(motor_pwm[m], 1)
            row[f"motor_rpm_{m + 1}"] = round(motor_rpm[m], 1)
            row[f"esc_current_a_{m + 1}"] = round(current_a / _N_MOTORS, 2)
            row[f"esc_temp_c_{m + 1}"] = round(30.0 + rng.uniform(-3, 8), 2)

        rows.append(row)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a sample telemetry CSV for replay.")
    parser.add_argument("--output", default="data/sample_telemetry.csv", help="Output CSV path.")
    parser.add_argument("--duration", type=float, default=120.0, help="Flight duration in seconds.")
    parser.add_argument("--rate-hz", type=float, default=1.0, help="Sample rate in Hz.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed, for reproducibility.")
    args = parser.parse_args()

    rows = generate_rows(duration_s=args.duration, rate_hz=args.rate_hz, seed=args.seed)
    df = pd.DataFrame(rows)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"Wrote {len(df)} rows ({args.duration:.0f}s @ {args.rate_hz}Hz) to {output_path}")


if __name__ == "__main__":
    main()
