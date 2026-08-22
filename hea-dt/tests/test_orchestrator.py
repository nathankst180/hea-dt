"""End-to-end wiring test: one full pass through M1->M12 in mock mode
should succeed and return a well-formed CycleResult. Layer A (M1/M2/M3)
and Layer B (M4-M7) are now real; M8-M12 are still Phase 0 stubs. This is
the test that proves the layers are correctly wired together."""

from config.loader import load_config
from contracts.schemas import LogRecord
from modules.m1_telemetry import MockFaultConfig, MockTelemetrySource
from modules.m2_validation import SensorValidator
from modules.m3_twin_state import TwinStateEstimator
from modules.m5_structural_shm import StructuralHealthPredictor
from orchestrator.main import CycleResult, run_cycle


def _build_stateful_modules(config):
    telemetry_source = MockTelemetrySource(seed=123, faults=MockFaultConfig())
    validator = SensorValidator(config)
    estimator = TwinStateEstimator(config)
    structural_predictor = StructuralHealthPredictor(config)
    return telemetry_source, validator, estimator, structural_predictor


def test_run_cycle_mock_mode_returns_cycle_result():
    config = load_config("config/config.yaml")
    telemetry_source, validator, estimator, structural_predictor = _build_stateful_modules(config)

    result = run_cycle(config, telemetry_source, validator, estimator, structural_predictor)

    assert isinstance(result, CycleResult)
    assert isinstance(result.log_record, LogRecord)
    assert result.decision is not None
    assert result.command is not None
    assert result.ack is not None
    # MockTelemetrySource has no live drone, so M11's stub should fabricate
    # an accepted ack rather than raising NotImplementedError.
    assert result.ack.accepted is True
    assert result.log_record.record_hash != ""


def test_run_cycle_chains_hashes_across_cycles():
    config = load_config("config/config.yaml")
    telemetry_source, validator, estimator, structural_predictor = _build_stateful_modules(config)

    first = run_cycle(config, telemetry_source, validator, estimator, structural_predictor)
    second = run_cycle(config, telemetry_source, validator, estimator, structural_predictor)

    assert second.log_record.prev_hash == first.log_record.record_hash
    assert second.log_record.record_hash != first.log_record.record_hash


def test_run_cycle_populates_twin_params_from_real_layer_a():
    """Layer A is no longer a stub: mass/resistance should come from the
    EKF (not the old fixed Phase 0 placeholders) and motor_thrust_efficiency
    should be sized to the mock's motor count."""
    config = load_config("config/config.yaml")
    telemetry_source, validator, estimator, _ = _build_stateful_modules(config)

    raw = telemetry_source.get_raw_state()
    state = validator.validate(raw)
    twin = estimator.estimate(state)

    assert twin.effective_mass_kg > 0.0
    assert len(twin.motor_thrust_efficiency) == len(raw.motor_rpm)
    assert twin.deviation >= 0.0


def test_run_cycle_populates_layer_b_predictions():
    """Layer B (M4-M7) is no longer a stub either: HealthElectrical/
    HealthStructural/EnergyPlan/Confidence should carry real, cycle-specific
    values (not Phase 0's fixed 'always healthy, always confident' stub
    output) — assuming the models have been trained (see README's Layer B
    section); if not, this still passes since the fallback stub behavior
    is itself well-formed."""
    config = load_config("config/config.yaml")
    telemetry_source, validator, estimator, structural_predictor = _build_stateful_modules(config)

    result = run_cycle(config, telemetry_source, validator, estimator, structural_predictor)

    assert 0.0 <= result.health_elec.battery_soh_pct <= 100.0
    assert len(result.health_elec.motor_health) == len(result.raw.motor_rpm)
    assert len(result.health_struct.per_rotor_scores) == len(result.raw.motor_rpm)
    assert result.energy_plan.v_opt_ms >= 0.0
    assert 0.0 <= result.confidence.decision_confidence <= 1.0
