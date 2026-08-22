"""Tests for M1: MockTelemetrySource (read side + write side + fault
injection) and MAVLinkGateway's connection-failure behavior (no live SITL
needed — we just prove it fails fast and clearly rather than hanging)."""

import time

import pytest

from modules.m1_telemetry import MAVLinkGateway, MockFaultConfig, MockTelemetrySource


def test_mock_source_produces_valid_raw_state():
    source = MockTelemetrySource(seed=1)
    raw = source.get_raw_state()

    assert raw.timestamp > 0
    assert 6.0 < raw.battery_voltage_v < 20.0
    assert len(raw.motor_pwm) == 4
    assert len(raw.motor_rpm) == 4
    assert raw.gps_fix_type == 3
    assert raw.armed is True


def test_mock_source_fills_ring_buffer():
    source = MockTelemetrySource(seed=1, ring_buffer_size=5)
    for _ in range(8):
        source.get_raw_state()
    assert len(source.ring_buffer) == 5
    assert len(source.recent(3)) == 3


def test_mock_source_battery_decline_fault_lowers_voltage_over_time():
    faults = MockFaultConfig(battery_decline_v_per_s=5.0)  # aggressive, so a short sleep is enough
    source = MockTelemetrySource(seed=2, faults=faults)

    first = source.get_raw_state()
    time.sleep(0.2)
    later = source.get_raw_state()

    assert later.battery_voltage_v < first.battery_voltage_v


def test_mock_source_gps_glitch_injection():
    faults = MockFaultConfig(glitch_prob=1.0, glitch_field="gps")  # always fires
    source = MockTelemetrySource(seed=3, faults=faults)
    raw = source.get_raw_state()
    assert raw.gps_fix_type == 0
    assert raw.gps_satellites_visible == 0


def test_mock_source_battery_voltage_glitch_injection():
    faults = MockFaultConfig(glitch_prob=1.0, glitch_field="battery_voltage")
    source = MockTelemetrySource(seed=5, faults=faults)
    raw = source.get_raw_state()
    assert raw.battery_voltage_v in (0.0, 99.0)


def test_mock_source_send_test_command_returns_accepted_ack():
    source = MockTelemetrySource(seed=4)
    ack = source.send_test_command(speed_ms=12.0)
    assert ack.accepted is True
    assert ack.latency_ms >= 0.0


def test_mavlink_gateway_raises_when_no_sitl_running():
    # Binding a local UDP port with nothing sending heartbeats should time
    # out quickly and raise a clear ConnectionError, not hang forever.
    gateway = MAVLinkGateway(connection_string="udpin:127.0.0.1:19999", heartbeat_timeout_s=0.3)
    with pytest.raises(ConnectionError):
        gateway.connect()
