"""
modules/m4_electrical_rul/features.py

Feature extraction for M4 — Electrical & Powertrain RUL Prediction.

Pure, stateless functions shared between TRAINING
(data/generate_fault_data.py, modules/m4_electrical_rul/train.py) and
INFERENCE (predictor.py), so the trained model always sees exactly the
feature representation it was trained on.

Battery features lean on M3's own TwinParams (battery_internal_resistance_ohm,
deviation) rather than re-deriving degradation from raw telemetry: M3's EKF
has ALREADY turned noisy raw readings into a physically-grounded resistance
estimate. M4's job is the FORWARD-LOOKING part — learning the mapping from
"current resistance and how hard it's being used" to "how much longer
until this becomes critical" — not re-deriving resistance from scratch.
"""

from __future__ import annotations

from contracts.schemas import StateVector, TwinParams

BATTERY_FEATURE_NAMES = [
    "battery_voltage_v",
    "battery_current_a",
    "battery_remaining_pct",
    "battery_temp_c",
    "twin_r_batt_ohm",
    "twin_mass_kg",
    "twin_deviation",
    "sensor_fault_count",
]

MOTOR_FEATURE_NAMES = [
    "motor_pwm",
    "motor_rpm",
    "esc_current_a",
    "esc_temp_c",
    "rpm_per_pwm",
    "rpm_deviation_from_fleet",
    "current_deviation_from_fleet",
    "twin_motor_efficiency",
]


def extract_battery_features(state: StateVector, twin: TwinParams) -> dict[str, float]:
    """One feature row for the battery RUL regressor, for this cycle."""
    raw = state.raw
    return {
        "battery_voltage_v": raw.battery_voltage_v if raw.battery_voltage_v is not None else 0.0,
        "battery_current_a": raw.battery_current_a if raw.battery_current_a is not None else 0.0,
        "battery_remaining_pct": raw.battery_remaining_pct if raw.battery_remaining_pct is not None else 50.0,
        "battery_temp_c": raw.battery_temp_c if raw.battery_temp_c is not None else 25.0,
        "twin_r_batt_ohm": twin.battery_internal_resistance_ohm,
        "twin_mass_kg": twin.effective_mass_kg,
        "twin_deviation": twin.deviation,
        "sensor_fault_count": float(len(state.sensor_fault_flags)),
    }


def extract_motor_features(state: StateVector, twin: TwinParams) -> list[dict[str, float]]:
    """One feature dict per motor (in motor-index order) for the
    weak-motor classifier. Empty list if there's no motor telemetry."""
    raw = state.raw
    n_motors = len(raw.motor_rpm)
    if n_motors == 0:
        return []

    mean_rpm = sum(raw.motor_rpm) / n_motors
    mean_current = sum(raw.esc_current_a) / n_motors if raw.esc_current_a else 0.0

    rows = []
    for i in range(n_motors):
        pwm = raw.motor_pwm[i] if i < len(raw.motor_pwm) else 1500.0
        rpm = raw.motor_rpm[i]
        current = raw.esc_current_a[i] if i < len(raw.esc_current_a) else 0.0
        temp = raw.esc_temp_c[i] if i < len(raw.esc_temp_c) else 25.0
        efficiency = twin.motor_thrust_efficiency[i] if i < len(twin.motor_thrust_efficiency) else 1.0
        rows.append(
            {
                "motor_pwm": pwm,
                "motor_rpm": rpm,
                "esc_current_a": current,
                "esc_temp_c": temp,
                "rpm_per_pwm": rpm / pwm if pwm else 0.0,
                "rpm_deviation_from_fleet": rpm - mean_rpm,
                "current_deviation_from_fleet": current - mean_current,
                "twin_motor_efficiency": efficiency,
            }
        )
    return rows
