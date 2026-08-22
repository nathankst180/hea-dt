"""Tests for M6: the physics-based power model and speed/altitude
optimizer. No trained model here (M6 is analytic) — these check the
optimizer responds to TwinParams the way the physics documented in
optimizer.py's docstring says it should, verified empirically before
writing (see the module docstring's "ALTITUDE'S EFFECT" note — hover
power actually INCREASES with altitude in this model, so alt_opt pins
near the floor, not the ceiling)."""

from config.loader import load_config
from contracts.schemas import RawState, StateVector, TwinParams
from modules.m6_energy.optimizer import optimize


def _state_and_twin(mass=1.5, efficiency=1.0, r_batt=0.05, wind_ned=(0.0, 0.0, 0.0), remaining_pct=80.0):
    raw = RawState(
        timestamp=0.0,
        battery_voltage_v=16.0,
        battery_current_a=9.0,
        battery_remaining_pct=remaining_pct,
        wind_ned=wind_ned,
    )
    state = StateVector(raw=raw, validity={}, signal_sigma={}, sensor_fault_flags=[])
    twin = TwinParams(
        timestamp=0.0,
        effective_mass_kg=mass,
        motor_thrust_efficiency=[efficiency] * 4,
        battery_internal_resistance_ohm=r_batt,
        deviation=0.0,
    )
    return state, twin


def _config():
    return load_config("config/config.yaml")


def test_returns_energy_plan_with_sane_fields():
    config = _config()
    state, twin = _state_and_twin()
    plan = optimize(state, twin, config)

    assert plan.v_opt_ms >= 0.0
    assert config.thresholds.min_altitude_m <= plan.alt_opt_m <= config.m6.alt_search_max_m
    assert plan.predicted_energy_wh >= 0.0


def test_heavier_aircraft_increases_optimal_speed():
    config = _config()
    light_state, light_twin = _state_and_twin(mass=1.5)
    heavy_state, heavy_twin = _state_and_twin(mass=4.0)

    light_plan = optimize(light_state, light_twin, config)
    heavy_plan = optimize(heavy_state, heavy_twin, config)

    # Heavier aircraft -> higher hover power -> the balance point between
    # induced and parasitic power shifts to a higher speed (see docstring).
    assert heavy_plan.v_opt_ms > light_plan.v_opt_ms
    assert heavy_plan.predicted_energy_wh > light_plan.predicted_energy_wh


def test_degraded_motor_efficiency_increases_optimal_speed_and_energy():
    config = _config()
    healthy_state, healthy_twin = _state_and_twin(efficiency=1.0)
    degraded_state, degraded_twin = _state_and_twin(efficiency=0.5)

    healthy_plan = optimize(healthy_state, healthy_twin, config)
    degraded_plan = optimize(degraded_state, degraded_twin, config)

    assert degraded_plan.v_opt_ms > healthy_plan.v_opt_ms
    assert degraded_plan.predicted_energy_wh > healthy_plan.predicted_energy_wh


def test_higher_battery_resistance_increases_energy_but_not_optimal_speed():
    config = _config()
    fresh_state, fresh_twin = _state_and_twin(r_batt=0.05)
    worn_state, worn_twin = _state_and_twin(r_batt=0.08)  # below the efficiency floor's saturation point

    fresh_plan = optimize(fresh_state, fresh_twin, config)
    worn_plan = optimize(worn_state, worn_twin, config)

    # Resistive losses scale power UNIFORMLY across all speeds, so they
    # change the predicted energy but not WHERE the minimum falls —
    # documented in optimizer.py as physically correct, not a limitation.
    assert worn_plan.predicted_energy_wh > fresh_plan.predicted_energy_wh
    assert abs(worn_plan.v_opt_ms - fresh_plan.v_opt_ms) < 1e-6


def test_wind_changes_optimal_ground_speed():
    config = _config()
    calm_state, calm_twin = _state_and_twin(wind_ned=(0.0, 0.0, 0.0))
    windy_state, windy_twin = _state_and_twin(wind_ned=(8.0, 0.0, 0.0))

    calm_plan = optimize(calm_state, calm_twin, config)
    windy_plan = optimize(windy_state, windy_twin, config)

    assert windy_plan.v_opt_ms != calm_plan.v_opt_ms


def test_low_battery_reduces_energy_margin():
    config = _config()
    full_state, full_twin = _state_and_twin(remaining_pct=90.0)
    low_state, low_twin = _state_and_twin(remaining_pct=25.0)

    full_plan = optimize(full_state, full_twin, config)
    low_plan = optimize(low_state, low_twin, config)

    assert low_plan.energy_margin_wh < full_plan.energy_margin_wh
