"""Tests for M4: the trained battery RUL regressor and weak-motor
classifier, exercised through predictor.py's predict(). Uses the actual
models trained by `python -m modules.m4_electrical_rul.train` against
data/battery_dataset.parquet / data/motor_dataset.parquet — these tests
assume that's already been run (same as the project's normal workflow;
see README's Layer B section)."""

import pytest

from config.loader import load_config
from contracts.schemas import RawState, StateVector, TwinParams
from modules.m4_electrical_rul import predictor

N_MOTORS = 4


def _state_and_twin(
    battery_voltage_v=16.0,
    battery_current_a=9.0,
    battery_remaining_pct=80.0,
    r_batt_ohm=0.05,
    motor_rpm=None,
    esc_current_a=None,
    motor_efficiency=None,
    deviation=0.5,
):
    raw = RawState(
        timestamp=0.0,
        battery_voltage_v=battery_voltage_v,
        battery_current_a=battery_current_a,
        battery_remaining_pct=battery_remaining_pct,
        battery_temp_c=25.0,
        motor_pwm=[1500.0] * N_MOTORS,
        motor_rpm=motor_rpm if motor_rpm is not None else [5000.0] * N_MOTORS,
        esc_current_a=esc_current_a if esc_current_a is not None else [2.25] * N_MOTORS,
        esc_temp_c=[30.0] * N_MOTORS,
        accel_xyz=(0.0, 0.0, 9.81),
    )
    state = StateVector(raw=raw, validity={}, signal_sigma={}, sensor_fault_flags=[])
    twin = TwinParams(
        timestamp=0.0,
        effective_mass_kg=1.5,
        motor_thrust_efficiency=motor_efficiency if motor_efficiency is not None else [1.0] * N_MOTORS,
        battery_internal_resistance_ohm=r_batt_ohm,
        deviation=deviation,
    )
    return state, twin


@pytest.fixture(scope="module")
def config():
    return load_config("config/config.yaml")


@pytest.fixture(scope="module")
def models_present(config):
    from pathlib import Path

    battery_path = Path(config.paths.models_dir) / "m4_battery_rul.joblib"
    if not battery_path.exists():
        pytest.skip("M4 models not trained — run `python -m modules.m4_electrical_rul.train` first")


def test_predict_returns_health_electrical(config, models_present):
    state, twin = _state_and_twin()
    health = predictor.predict(state, twin, config)

    assert 0.0 <= health.battery_soh_pct <= 100.0
    assert health.battery_rul_s >= 0.0
    assert len(health.motor_health) == N_MOTORS
    assert all(0.0 <= h <= 1.0 for h in health.motor_health)
    assert 0.0 <= health.p_fail_horizon <= 1.0


def test_healthy_battery_scores_high_soh_and_long_rul(config, models_present):
    state, twin = _state_and_twin(r_batt_ohm=0.05)  # nominal, matches training's healthy baseline
    health = predictor.predict(state, twin, config)

    assert health.battery_soh_pct > 90.0


def test_degraded_battery_scores_low_soh_and_short_rul(config, models_present):
    healthy_state, healthy_twin = _state_and_twin(r_batt_ohm=0.05)
    degraded_state, degraded_twin = _state_and_twin(r_batt_ohm=0.14)  # near R_FAIL_OHM=0.15

    healthy_health = predictor.predict(healthy_state, healthy_twin, config)
    degraded_health = predictor.predict(degraded_state, degraded_twin, config)

    assert degraded_health.battery_soh_pct < healthy_health.battery_soh_pct
    assert degraded_health.battery_rul_s < healthy_health.battery_rul_s
    assert degraded_health.p_fail_horizon > healthy_health.p_fail_horizon


def test_weak_motor_scores_lower_health_than_its_siblings(config, models_present):
    # Same signature the "severe" weak_motor training scenario injects:
    # motor 2 undershoots RPM and draws excess current for it.
    rpm = [5000.0, 5000.0, 2000.0, 5000.0]
    esc_current = [2.25, 2.25, 3.6, 2.25]
    efficiency = [1.0, 1.0, 0.4, 1.0]
    state, twin = _state_and_twin(motor_rpm=rpm, esc_current_a=esc_current, motor_efficiency=efficiency)

    health = predictor.predict(state, twin, config)

    weak_health = health.motor_health[2]
    sibling_health = [h for i, h in enumerate(health.motor_health) if i != 2]
    assert weak_health < min(sibling_health)


def test_p_fail_horizon_increases_with_configured_horizon(config, models_present):
    state, twin = _state_and_twin(r_batt_ohm=0.12)  # meaningfully degraded, so RUL isn't effectively infinite

    short_config = load_config("config/config.yaml")
    short_config.thresholds.p_fail_horizon_s = 10.0
    long_config = load_config("config/config.yaml")
    long_config.thresholds.p_fail_horizon_s = 3000.0

    p_fail_short = predictor.predict(state, twin, short_config).p_fail_horizon
    p_fail_long = predictor.predict(state, twin, long_config).p_fail_horizon

    assert p_fail_long > p_fail_short


def test_falls_back_to_healthy_stub_when_models_missing(config, tmp_path):
    predictor._load_models.cache_clear()
    broken_config = load_config("config/config.yaml")
    broken_config.paths.models_dir = str(tmp_path / "no_models_here")

    state, twin = _state_and_twin()
    health = predictor.predict(state, twin, broken_config)

    assert health.battery_soh_pct == 100.0
    assert health.battery_rul_s == float("inf")
    assert health.motor_health == [1.0] * N_MOTORS

    predictor._load_models.cache_clear()  # don't leak the empty-dir cache entry into other tests
