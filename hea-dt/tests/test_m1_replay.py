"""Tests for M1: ReplayTelemetrySource and load_telemetry_file. Covers
column parsing (scalars, list-valued, 3-vector, position), missing-column
defaults, sort-by-timestamp, playback pacing at a speed multiplier,
exhaustion, and both file formats (CSV + Parquet)."""

import time

import pandas as pd
import pytest

from modules.m1_telemetry import ReplayTelemetrySource, TelemetryFormatError, load_telemetry_file


def _write_csv(tmp_path, rows: list[dict], name: str = "flight.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _row(t: float, **overrides) -> dict:
    base = {
        "timestamp": t,
        "battery_voltage_v": 16.0,
        "battery_current_a": 10.0,
        "gps_fix_type": 3,
        "gps_satellites_visible": 12,
        "armed": True,
        "flight_mode": "AUTO",
        "motor_pwm_1": 1500.0,
        "motor_pwm_2": 1500.0,
        "motor_pwm_3": 1500.0,
        "motor_pwm_4": 1500.0,
        "accel_xyz_x": 0.0,
        "accel_xyz_y": 0.0,
        "accel_xyz_z": 9.81,
        "position_lat": 47.4,
        "position_lon": 8.5,
        "position_alt_m": 100.0,
    }
    base.update(overrides)
    return base


def test_load_telemetry_file_parses_scalar_list_vector_and_position_columns(tmp_path):
    path = _write_csv(tmp_path, [_row(0.0)])
    rows = load_telemetry_file(path)

    assert len(rows) == 1
    raw = rows[0]
    assert raw.timestamp == 0.0
    assert raw.battery_voltage_v == 16.0
    assert raw.motor_pwm == [1500.0, 1500.0, 1500.0, 1500.0]
    assert raw.accel_xyz == (0.0, 0.0, 9.81)
    assert raw.position_lla == (47.4, 8.5, 100.0)
    # gps_fix_type/satellites were provided -> parsed; anything NOT in the
    # CSV (e.g. wind_ned) should fall back to RawState's own default.
    assert raw.gps_fix_type == 3
    assert raw.wind_ned == (0.0, 0.0, 0.0)


def test_load_telemetry_file_missing_column_falls_back_to_rawstate_default(tmp_path):
    # No motor_rpm_* columns at all in this file.
    path = _write_csv(tmp_path, [_row(0.0)])
    rows = load_telemetry_file(path)
    assert rows[0].motor_rpm == []


def test_load_telemetry_file_requires_timestamp_column(tmp_path):
    path = tmp_path / "no_timestamp.csv"
    pd.DataFrame([{"battery_voltage_v": 16.0}]).to_csv(path, index=False)

    with pytest.raises(TelemetryFormatError, match="timestamp"):
        load_telemetry_file(path)


def test_load_telemetry_file_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "flight.txt"
    path.write_text("not telemetry")
    with pytest.raises(TelemetryFormatError, match="unsupported file extension"):
        load_telemetry_file(path)


def test_load_telemetry_file_sorts_rows_by_timestamp(tmp_path):
    # Deliberately out of order in the file.
    path = _write_csv(tmp_path, [_row(5.0, battery_voltage_v=15.0), _row(0.0, battery_voltage_v=16.0), _row(2.0, battery_voltage_v=15.5)])
    rows = load_telemetry_file(path)
    assert [r.timestamp for r in rows] == [0.0, 2.0, 5.0]
    assert rows[0].battery_voltage_v == 16.0


def test_load_telemetry_file_parquet_format(tmp_path):
    path = tmp_path / "flight.parquet"
    pd.DataFrame([_row(0.0), _row(1.0)]).to_parquet(path)
    rows = load_telemetry_file(path)
    assert len(rows) == 2
    assert rows[0].timestamp == 0.0


def test_replay_source_exposes_row_count_and_duration(tmp_path):
    path = _write_csv(tmp_path, [_row(0.0), _row(1.0), _row(2.0)])
    source = ReplayTelemetrySource(path, speed_multiplier=100.0)
    assert source.total_rows == 3
    assert source.duration_s == 2.0


def test_replay_source_replays_in_order_and_exhausts(tmp_path):
    path = _write_csv(tmp_path, [_row(0.0, battery_voltage_v=16.0), _row(0.01, battery_voltage_v=15.9), _row(0.02, battery_voltage_v=15.8)])
    source = ReplayTelemetrySource(path, speed_multiplier=100.0)

    voltages = []
    while source.has_next():
        voltages.append(source.get_raw_state().battery_voltage_v)

    assert voltages == [16.0, 15.9, 15.8]
    assert source.has_next() is False
    with pytest.raises(StopIteration):
        source.get_raw_state()


def test_replay_source_paces_playback_by_speed_multiplier(tmp_path):
    # Three rows spanning 1.0s of recorded time, replayed at 10x -> should
    # take roughly 0.1s of wall-clock time (minus the free first row).
    path = _write_csv(tmp_path, [_row(0.0), _row(0.5), _row(1.0)])
    source = ReplayTelemetrySource(path, speed_multiplier=10.0)

    t0 = time.time()
    while source.has_next():
        source.get_raw_state()
    elapsed = time.time() - t0

    assert 0.05 < elapsed < 0.5  # generous bounds to avoid CI flakiness


def test_replay_source_ring_buffer_fills(tmp_path):
    path = _write_csv(tmp_path, [_row(float(i)) for i in range(5)])
    source = ReplayTelemetrySource(path, speed_multiplier=1000.0, ring_buffer_size=3)
    while source.has_next():
        source.get_raw_state()
    assert len(source.ring_buffer) == 3


def test_replay_source_send_test_command_returns_accepted_ack(tmp_path):
    path = _write_csv(tmp_path, [_row(0.0)])
    source = ReplayTelemetrySource(path, speed_multiplier=1000.0)
    ack = source.send_test_command(speed_ms=10.0)
    assert ack.accepted is True
