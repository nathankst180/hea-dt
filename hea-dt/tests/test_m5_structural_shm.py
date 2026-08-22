"""Tests for M5: StructuralHealthPredictor, exercised with the actual
trained models (see README's Layer B section for how to (re)train them).
Feeds synthetic windows shaped like the training scenarios — healthy
near-hover vs. the "severe" prop_imbalance injection — and checks the
classifier tells them apart, matching data/generate_fault_data.py's
PROP_IMBALANCE_PARAMS."""

import math
import random

import pytest

from config.loader import load_config
from contracts.schemas import RawState, StateVector, TwinParams
from modules.m5_structural_shm import StructuralHealthPredictor

N_MOTORS = 4
RATE_HZ = 8.0


def _twin() -> TwinParams:
    return TwinParams(timestamp=0.0, effective_mass_kg=1.5, motor_thrust_efficiency=[1.0] * N_MOTORS,
                       battery_internal_resistance_ohm=0.05, deviation=0.3)


def _healthy_state(t: float, rng: random.Random) -> StateVector:
    raw = RawState(
        timestamp=t,
        battery_voltage_v=16.0,
        battery_current_a=9.0,
        motor_rpm=[5000.0 + rng.uniform(-40, 40) for _ in range(N_MOTORS)],
        motor_pwm=[1500.0] * N_MOTORS,
        accel_xyz=(rng.uniform(-0.1, 0.1), rng.uniform(-0.1, 0.1), 9.81 + rng.uniform(-0.05, 0.05)),
        gyro_xyz=(rng.uniform(-0.005, 0.005),) * 3,
    )
    return StateVector(raw=raw, validity={}, signal_sigma={}, sensor_fault_flags=[])


def _imbalanced_state(t: float, rng: random.Random, rotor_idx: int, amp: float, freq: float, phase: float) -> StateVector:
    osc = amp * math.sin(2 * math.pi * freq * t + phase)
    rpm = [5000.0 + rng.uniform(-40, 40) for _ in range(N_MOTORS)]
    rpm[rotor_idx] += 60.0 * math.sin(2 * math.pi * freq * t + phase)
    raw = RawState(
        timestamp=t,
        battery_voltage_v=16.0,
        battery_current_a=9.0,
        motor_rpm=rpm,
        motor_pwm=[1500.0] * N_MOTORS,
        accel_xyz=(rng.uniform(-0.1, 0.1) + osc, rng.uniform(-0.1, 0.1) + 0.4 * osc, 9.81 + rng.uniform(-0.05, 0.05)),
        gyro_xyz=(rng.uniform(-0.005, 0.005), rng.uniform(-0.005, 0.005), rng.uniform(-0.005, 0.005) + 0.05 * osc),
    )
    return StateVector(raw=raw, validity={}, signal_sigma={}, sensor_fault_flags=[])


@pytest.fixture(scope="module")
def config():
    return load_config("config/config.yaml")


@pytest.fixture(scope="module")
def models_present(config):
    from pathlib import Path

    if not (Path(config.paths.models_dir) / "m5_imbalance_classifier.joblib").exists():
        pytest.skip("M5 models not trained — run `python -m modules.m5_structural_shm.train` first")


def test_predict_returns_health_structural(config, models_present):
    predictor = StructuralHealthPredictor(config)
    rng = random.Random(0)
    twin = _twin()

    health = None
    for i in range(10):
        health = predictor.predict(_healthy_state(i / RATE_HZ, rng), twin)

    assert len(health.per_rotor_scores) == N_MOTORS
    assert 0.0 <= health.structural_anomaly_score <= 1.0
    assert 0.0 <= health.prop_damage_score <= 1.0


def test_healthy_signal_not_flagged_as_imbalance(config, models_present):
    predictor = StructuralHealthPredictor(config)
    rng = random.Random(1)
    twin = _twin()

    health = None
    for i in range(40):
        health = predictor.predict(_healthy_state(i / RATE_HZ, rng), twin)

    assert health.imbalance_flag is False
    assert health.structural_anomaly_score < 0.5


def test_severe_imbalance_is_flagged(config, models_present):
    predictor = StructuralHealthPredictor(config)
    rng = random.Random(2)
    twin = _twin()
    amp, freq = 1.8, 2.2  # matches data/generate_fault_data.py's PROP_IMBALANCE_PARAMS["severe"]
    phase = rng.uniform(0, 2 * math.pi)

    health = None
    for i in range(40):
        health = predictor.predict(_imbalanced_state(i / RATE_HZ, rng, rotor_idx=1, amp=amp, freq=freq, phase=phase), twin)

    assert health.imbalance_flag is True
    assert health.structural_anomaly_score > 0.5


def test_falls_back_to_no_anomaly_when_models_missing(config, tmp_path):
    from modules.m5_structural_shm import predictor as predictor_module

    predictor_module._load_models.cache_clear()
    broken_config = load_config("config/config.yaml")
    broken_config.paths.models_dir = str(tmp_path / "no_models_here")

    predictor = StructuralHealthPredictor(broken_config)
    rng = random.Random(3)
    health = predictor.predict(_healthy_state(0.0, rng), _twin())

    assert health.imbalance_flag is False
    assert health.structural_anomaly_score == 0.0

    predictor_module._load_models.cache_clear()
