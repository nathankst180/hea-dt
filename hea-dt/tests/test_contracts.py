"""Sanity checks that every shared data contract can be instantiated with
defaults, and that the enums round-trip through their string values. Real
per-module behavior tests land alongside each module as it's implemented."""

from contracts.schemas import (
    Ack,
    Command,
    CommandKind,
    Confidence,
    Decision,
    DecisionKind,
    EnergyPlan,
    Feasibility,
    HealthElectrical,
    HealthStructural,
    LogRecord,
    RawState,
    StateVector,
    TwinParams,
)


def test_raw_state_defaults():
    raw = RawState(timestamp=0.0)
    assert raw.motor_pwm == []
    assert raw.armed is False


def test_state_vector_wraps_raw_state():
    raw = RawState(timestamp=0.0)
    state = StateVector(raw=raw)
    assert state.raw is raw
    assert state.sensor_fault_flags == []


def test_twin_params_defaults():
    twin = TwinParams(timestamp=0.0)
    assert twin.deviation == 0.0


def test_health_electrical_defaults():
    health = HealthElectrical(timestamp=0.0)
    assert health.battery_soh_pct == 100.0
    assert health.battery_rul_s == float("inf")


def test_health_structural_defaults():
    health = HealthStructural(timestamp=0.0)
    assert health.imbalance_flag is False


def test_energy_plan_defaults():
    plan = EnergyPlan(timestamp=0.0)
    assert plan.optimized_waypoints == []


def test_confidence_defaults():
    conf = Confidence(timestamp=0.0)
    assert conf.decision_confidence == 1.0


def test_feasibility_defaults():
    feas = Feasibility(timestamp=0.0)
    assert feas.feasible is True


def test_decision_kind_values():
    assert DecisionKind.NOMINAL == "NOMINAL"
    decision = Decision(timestamp=0.0, kind=DecisionKind.ABORT)
    assert decision.kind == DecisionKind.ABORT


def test_command_kind_values():
    assert CommandKind.SET_SPEED == "SET_SPEED"
    command = Command(timestamp=0.0, kind=CommandKind.LAND)
    assert command.reversible is True


def test_ack_defaults():
    ack = Ack(timestamp=0.0)
    assert ack.accepted is False


def test_log_record_requires_decision():
    decision = Decision(timestamp=0.0)
    record = LogRecord(timestamp=0.0, decision=decision)
    assert record.command is None
    assert record.ack is None
