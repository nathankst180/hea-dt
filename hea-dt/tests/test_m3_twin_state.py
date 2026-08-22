"""Tests for M3: TwinStateEstimator. Confirms the deviation metric stays
low on nominal, self-consistent telemetry, and spikes when fed data that
contradicts the twin's converged model - e.g. a battery voltage that's
collapsed far below what the estimated internal resistance can explain,
simulating a battery draining/degrading much faster than expected."""

from config.loader import load_config
from contracts.schemas import RawState, StateVector
from modules.m3_twin_state import TwinStateEstimator


def _state(battery_voltage_v, battery_current_a, battery_remaining_pct, vertical_accel, motor_rpm=None):
    raw = RawState(
        timestamp=0.0,
        battery_voltage_v=battery_voltage_v,
        battery_current_a=battery_current_a,
        battery_remaining_pct=battery_remaining_pct,
        battery_temp_c=25.0,
        motor_rpm=motor_rpm if motor_rpm is not None else [5000.0] * 4,
        motor_pwm=[1500.0] * 4,
        accel_xyz=(0.0, 0.0, vertical_accel),
    )
    return StateVector(raw=raw)


def test_deviation_stays_low_on_nominal_hover_data():
    config = load_config("config/config.yaml")
    estimator = TwinStateEstimator(config)

    # Roughly consistent with the estimator's own physics assumptions:
    # V_oc(80%) ~ 16.08V, R~0.05ohm initial guess, I=10A -> ~15.58V predicted.
    deviation = None
    for _ in range(10):
        twin = estimator.estimate(_state(15.5, 10.0, 80.0, 9.81))
        deviation = twin.deviation

    # deviation should settle down as the filter converges, not blow up
    assert deviation < 5.0


def test_deviation_spikes_when_battery_drains_faster_than_modeled():
    config = load_config("config/config.yaml")
    estimator = TwinStateEstimator(config)

    twin = None
    for _ in range(8):
        twin = estimator.estimate(_state(15.5, 10.0, 80.0, 9.81))
    baseline_deviation = twin.deviation

    # Same current draw and charge level, but voltage has collapsed far
    # below what the converged internal-resistance estimate predicts -
    # exactly what a battery failing/degrading faster than modeled looks
    # like from the outside.
    bad_twin = estimator.estimate(_state(9.0, 10.0, 78.0, 9.81))

    assert bad_twin.deviation > baseline_deviation
    assert bad_twin.deviation > 3.0


def test_effective_mass_stays_physically_plausible():
    config = load_config("config/config.yaml")
    estimator = TwinStateEstimator(config)

    twin = None
    for _ in range(10):
        twin = estimator.estimate(_state(15.5, 10.0, 80.0, 9.81))

    assert 0.1 <= twin.effective_mass_kg <= 20.0


def test_missing_motor_rpm_does_not_explode_deviation():
    # If ESC_STATUS isn't available, motor_rpm is an empty list; the
    # estimator should fall back to a nominal hover RPM rather than
    # treating "no data" as "zero thrust."
    config = load_config("config/config.yaml")
    estimator = TwinStateEstimator(config)

    twin = estimator.estimate(_state(15.5, 10.0, 80.0, 9.81, motor_rpm=[]))
    assert twin.deviation < 20.0
