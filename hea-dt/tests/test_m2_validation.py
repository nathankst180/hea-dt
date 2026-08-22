"""Tests for M2: SensorValidator. Covers: clean data passes through
unchanged, an isolated sensor glitch gets flagged as a SENSOR fault (not
silently treated as a real component problem) and doesn't leak downstream,
a gap gets imputed, a genuine gradual trend is NOT flagged as a sensor
fault, and the GPS/IMU cross-sensor consistency check."""

from config.loader import load_config
from contracts.schemas import RawState
from modules.m2_validation import SensorValidator


def _raw_state(**overrides) -> RawState:
    defaults = dict(
        timestamp=1000.0,
        battery_voltage_v=16.0,
        battery_current_a=10.0,
        battery_remaining_pct=80.0,
        battery_temp_c=25.0,
        motor_pwm=[1500.0] * 4,
        motor_rpm=[5000.0] * 4,
        esc_current_a=[2.5] * 4,
        esc_temp_c=[30.0] * 4,
        accel_xyz=(0.0, 0.0, 9.81),
        gyro_xyz=(0.0, 0.0, 0.0),
        attitude_rpy=(0.0, 0.0, 0.0),
        position_lla=(47.4, 8.5, 100.0),
        velocity_ned=(10.0, 0.0, 0.0),
        airspeed_ms=10.0,
        groundspeed_ms=10.0,
        wind_ned=(0.0, 0.0, 0.0),
        gps_fix_type=3,
        gps_satellites_visible=12,
        armed=True,
        flight_mode="AUTO",
    )
    defaults.update(overrides)
    return RawState(**defaults)


def _validator() -> SensorValidator:
    config = load_config("config/config.yaml")
    return SensorValidator(config)


def test_clean_data_passes_through_unchanged():
    validator = _validator()
    raw = _raw_state()
    state = validator.validate(raw)

    assert state.raw.battery_voltage_v == raw.battery_voltage_v
    assert state.sensor_fault_flags == []
    assert all(state.validity.get(k, True) for k in ("battery_voltage_v", "battery_current_a", "battery_temp_c"))


def test_isolated_glitch_flagged_as_sensor_fault_not_propagated():
    validator = _validator()

    # Build up a few cycles of stable, healthy telemetry so the validator
    # has a "normal" recent history to compare against.
    last_good = None
    for i in range(5):
        raw = _raw_state(timestamp=1000.0 + i, battery_voltage_v=16.0 + 0.01 * i)
        state = validator.validate(raw)
        last_good = state.raw.battery_voltage_v

    # A single-cycle, physically-impossible spike - current/temp/everything
    # else stays completely normal, which is the signature of a SENSOR
    # glitch, not a real battery event (a real failing battery wouldn't
    # jump to 99V for one cycle with nothing else changing).
    glitchy = _raw_state(timestamp=1005.0, battery_voltage_v=99.0)
    state = validator.validate(glitchy)

    assert "battery_voltage_v" in state.sensor_fault_flags
    assert state.validity["battery_voltage_v"] is False
    # The glitch value must not leak downstream as if it were real health
    # data — it should be clamped back to the last known-good reading.
    assert state.raw.battery_voltage_v == last_good


def test_gradual_real_trend_is_not_flagged_as_sensor_fault():
    validator = _validator()

    # A slow, steady decline across many cycles - this is what a REAL
    # degrading battery looks like, and M2 must let it through untouched
    # so M4 can see it, rather than "fixing" it like a glitch.
    for i in range(15):
        raw = _raw_state(timestamp=1000.0 + i, battery_voltage_v=16.0 - 0.05 * i, battery_current_a=10.0)
        state = validator.validate(raw)
        assert "battery_voltage_v" not in state.sensor_fault_flags


def test_gap_gets_imputed_from_last_known_good():
    validator = _validator()

    good = _raw_state(timestamp=1000.0, battery_voltage_v=16.2)
    validator.validate(good)

    gap = _raw_state(timestamp=1001.0, battery_voltage_v=None)
    state = validator.validate(gap)

    assert state.raw.battery_voltage_v == 16.2
    assert state.validity["battery_voltage_v_imputed"] is True


def test_gap_with_no_history_yet_is_marked_not_imputed():
    validator = _validator()
    gap = _raw_state(timestamp=1000.0, battery_voltage_v=None)
    state = validator.validate(gap)

    assert state.raw.battery_voltage_v is None
    assert state.validity["battery_voltage_v_imputed"] is False


def test_gps_fix_loss_flagged_as_sensor_fault():
    validator = _validator()

    # gps_satellites_visible=0 is numerically "in range" (0-32), so this
    # specifically exercises the dedicated fix-quality check rather than
    # the range check - a lost GPS fix must not silently pass through just
    # because the satellite count alone still looks plausible.
    lost_fix = _raw_state(gps_fix_type=0, gps_satellites_visible=0)
    state = validator.validate(lost_fix)

    assert state.validity["gps_fix_type"] is False
    assert "gps_fix_type" in state.sensor_fault_flags


def test_gps_imu_disagreement_flags_gps_sensor_fault():
    validator = _validator()

    first = _raw_state(timestamp=2000.0, velocity_ned=(10.0, 0.0, 0.0), groundspeed_ms=10.0, accel_xyz=(0.0, 0.0, 9.81))
    validator.validate(first)

    # One second later, IMU shows essentially no horizontal acceleration
    # (so speed should be ~unchanged), but GPS suddenly reports a huge
    # jump in ground speed. GPS and IMU can't both be right, and there's
    # no powertrain component that could cause this, so it should be
    # attributed to a GPS sensor problem.
    jump = _raw_state(timestamp=2001.0, velocity_ned=(10.0, 0.0, 0.0), groundspeed_ms=40.0, accel_xyz=(0.0, 0.0, 9.81))
    state = validator.validate(jump)

    assert state.validity["gps_imu_consistency"] is False
    assert "groundspeed_ms" in state.sensor_fault_flags
    assert "gps_fix_type" in state.sensor_fault_flags
