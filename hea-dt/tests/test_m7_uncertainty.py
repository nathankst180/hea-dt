"""Tests for M7: the MAPIE conformal wrappers (calibrated by
`python -m modules.m7_uncertainty.calibration`, after M4/M5's train.py)
and the decision_confidence fusion formula. The key required behavior:
decision_confidence must drop when twin deviation rises, even with
confident underlying model predictions — see quantifier.py's docstring
for the exact multiplicative fusion formula this is testing."""

import pytest

from config.loader import load_config
from contracts.schemas import (
    EnergyPlan,
    HealthElectrical,
    HealthStructural,
    RawState,
    StateVector,
    TwinParams,
)
from modules.m7_uncertainty import quantifier

N_MOTORS = 4


def _twin(deviation: float) -> TwinParams:
    return TwinParams(
        timestamp=0.0,
        effective_mass_kg=1.5,
        motor_thrust_efficiency=[1.0] * N_MOTORS,
        battery_internal_resistance_ohm=0.05,
        deviation=deviation,
    )


def _state(sensor_fault_flags=None) -> StateVector:
    raw = RawState(
        timestamp=0.0,
        battery_voltage_v=16.0,
        battery_current_a=9.0,
        battery_remaining_pct=80.0,
        motor_rpm=[5000.0] * N_MOTORS,
    )
    return StateVector(raw=raw, validity={}, signal_sigma={}, sensor_fault_flags=sensor_fault_flags or [])


def _health_elec() -> HealthElectrical:
    return HealthElectrical(
        timestamp=0.0, battery_soh_pct=95.0, battery_rul_s=2000.0,
        motor_health=[1.0] * N_MOTORS, esc_health=[1.0] * N_MOTORS, p_fail_horizon=0.05, confidence=1.0,
    )


def _health_struct() -> HealthStructural:
    return HealthStructural(
        timestamp=0.0, imbalance_flag=False, prop_damage_score=0.0,
        structural_anomaly_score=0.02, per_rotor_scores=[0.1] * N_MOTORS, confidence=1.0,
    )


def _energy_plan() -> EnergyPlan:
    return EnergyPlan(timestamp=0.0, v_opt_ms=10.0, alt_opt_m=60.0, predicted_energy_wh=10.0, energy_margin_wh=20.0)


@pytest.fixture(scope="module")
def config():
    return load_config("config/config.yaml")


def test_quantify_returns_well_formed_confidence(config):
    result = quantifier.quantify(_health_elec(), _health_struct(), _energy_plan(), _twin(0.2), _state(), config)

    assert 0.0 <= result.decision_confidence <= 1.0
    assert "battery_rul" in result.per_prediction
    assert "structural_anomaly" in result.per_prediction
    assert "energy_margin" in result.per_prediction


def test_decision_confidence_drops_as_twin_deviation_rises(config):
    # Same everything else (confident, healthy-looking predictions) —
    # only twin.deviation changes.
    health_elec, health_struct, energy_plan, state = _health_elec(), _health_struct(), _energy_plan(), _state()

    low_dev = quantifier.quantify(health_elec, health_struct, energy_plan, _twin(0.0), state, config)
    mid_dev = quantifier.quantify(health_elec, health_struct, energy_plan, _twin(3.0), state, config)
    high_dev = quantifier.quantify(health_elec, health_struct, energy_plan, _twin(9.0), state, config)

    assert low_dev.decision_confidence > mid_dev.decision_confidence > high_dev.decision_confidence


def test_high_deviation_pulls_down_confidence_even_with_confident_predictions(config):
    """The specific scenario the design principle names: individual model
    predictions look confident (low anomaly score, healthy battery), but
    decision_confidence must still be low if twin deviation is high."""
    confident_health_elec = _health_elec()  # battery_soh=95, p_fail=0.05 — reads as healthy/confident
    confident_health_struct = _health_struct()  # anomaly_score=0.02 — reads as confident/no imbalance

    result = quantifier.quantify(
        confident_health_elec, confident_health_struct, _energy_plan(), _twin(9.0), _state(), config
    )

    assert result.decision_confidence < 0.3  # deviation_factor alone at 9.0/3.0 scale is exp(-3) ~= 0.05


def test_decision_confidence_drops_with_sensor_fault_count(config):
    health_elec, health_struct, energy_plan, twin = _health_elec(), _health_struct(), _energy_plan(), _twin(0.0)

    clean = quantifier.quantify(health_elec, health_struct, energy_plan, twin, _state(), config)
    faulted = quantifier.quantify(
        health_elec, health_struct, energy_plan, twin, _state(sensor_fault_flags=["battery_voltage_v", "gps_fix_type"]), config
    )

    assert faulted.decision_confidence < clean.decision_confidence


def test_deviation_fusion_still_applies_when_conformal_wrappers_missing(config, tmp_path):
    quantifier._load_conformal_wrappers.cache_clear()
    broken_config = load_config("config/config.yaml")
    broken_config.paths.models_dir = str(tmp_path / "no_models_here")

    low = quantifier.quantify(_health_elec(), _health_struct(), _energy_plan(), _twin(0.0), _state(), broken_config)
    high = quantifier.quantify(_health_elec(), _health_struct(), _energy_plan(), _twin(9.0), _state(), broken_config)

    assert low.decision_confidence > high.decision_confidence

    quantifier._load_conformal_wrappers.cache_clear()


def test_structural_feature_row_is_used_when_provided(config):
    healthy_features = {name: 0.0 for name in quantifier.STRUCTURAL_ALL_FEATURES}
    result = quantifier.quantify(
        _health_elec(), _health_struct(), _energy_plan(), _twin(0.0), _state(), config,
        structural_feature_row=healthy_features,
    )
    assert 0.0 <= result.per_prediction["structural_anomaly"] <= 1.0
