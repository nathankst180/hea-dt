"""
modules/m1_telemetry/replay_source.py

M1 — Replay telemetry source.

Feeds a previously-recorded telemetry file (CSV or Parquet) through the
REAL M2-M12 pipeline in original timestamp order, as if it were arriving
live. This is not a data viewer: every row runs through the actual
SensorValidator, TwinStateEstimator, and (once built) the real M4-M12
models, exactly like a live MAVLink stream would — orchestrator.run_cycle()
doesn't know or care whether get_raw_state() came from a socket or a file.

File format: one row per reading. `timestamp` (Unix epoch seconds) is the
only required column; every other RawState field is optional — a missing
column just leaves that field at its default (None / empty list / zeros),
same as a live gateway would for a message type it hasn't received yet.
List-valued fields (motor_pwm, motor_rpm, esc_current_a, esc_temp_c) are
stored as flat columns "<field>_1".."<field>_N"; 3-vector fields
(accel_xyz, gyro_xyz, attitude_rpy, velocity_ned, wind_ned) are stored as
"<field>_x/_y/_z"; position_lla is stored as position_lat/position_lon/
position_alt_m. See scripts/generate_sample_telemetry.py for a working
example file with every column populated.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from contracts.schemas import Ack, RawState
from modules.m1_telemetry.base import TelemetrySource

logger = logging.getLogger("hea_dt.m1_telemetry")

# Scalar columns map 1:1 onto RawState fields of the same name.
_SCALAR_FIELDS = (
    "timestamp",
    "battery_voltage_v",
    "battery_current_a",
    "battery_remaining_pct",
    "battery_temp_c",
    "airspeed_ms",
    "groundspeed_ms",
    "gps_fix_type",
    "gps_satellites_visible",
    "armed",
    "flight_mode",
)

_INT_FIELDS = ("gps_fix_type", "gps_satellites_visible")
_FLOAT_FIELDS = (
    "timestamp",
    "battery_voltage_v",
    "battery_current_a",
    "battery_remaining_pct",
    "battery_temp_c",
    "airspeed_ms",
    "groundspeed_ms",
)

# List-valued fields: N flat columns named "<field>_1".."<field>_N".
_LIST_FIELDS = ("motor_pwm", "motor_rpm", "esc_current_a", "esc_temp_c")

# 3-vector fields: "<field>_x", "<field>_y", "<field>_z".
_VECTOR3_FIELDS = ("accel_xyz", "gyro_xyz", "attitude_rpy", "velocity_ned", "wind_ned")

# position_lla is its own named columns since "x/y/z" would be misleading
# for latitude/longitude/altitude.
_POSITION_COLUMNS = {"position_lla": ("position_lat", "position_lon", "position_alt_m")}


class TelemetryFormatError(ValueError):
    """Raised when an uploaded file is missing required columns, has no
    usable rows, or otherwise can't be parsed into RawState rows."""


def load_telemetry_file(file_path: str | Path) -> list[RawState]:
    """Load a CSV or Parquet telemetry file into a timestamp-sorted list
    of RawState. Used both by ReplayTelemetrySource and by the /upload API
    endpoint (to validate a file before accepting it)."""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix == ".parquet":
        df = pd.read_parquet(file_path)
    elif suffix == ".csv":
        df = pd.read_csv(file_path)
    else:
        raise TelemetryFormatError(f"unsupported file extension {suffix!r} (expected .csv or .parquet)")

    if "timestamp" not in df.columns:
        raise TelemetryFormatError(
            "telemetry file is missing a required 'timestamp' column (Unix epoch seconds)"
        )

    df = df.dropna(subset=["timestamp"])
    if df.empty:
        raise TelemetryFormatError("telemetry file has a 'timestamp' column but every value is empty")

    df = df.sort_values("timestamp").reset_index(drop=True)

    rows = [_row_to_raw_state(row, df.columns) for _, row in df.iterrows()]
    return rows


def _row_to_raw_state(row, columns) -> RawState:
    kwargs: dict = {}

    for field_name in _SCALAR_FIELDS:
        if field_name in columns:
            value = row[field_name]
            kwargs[field_name] = None if pd.isna(value) else value

    for field_name in _LIST_FIELDS:
        values = []
        i = 1
        while f"{field_name}_{i}" in columns:
            v = row[f"{field_name}_{i}"]
            if not pd.isna(v):
                values.append(float(v))
            i += 1
        if values:
            kwargs[field_name] = values

    for field_name in _VECTOR3_FIELDS:
        cols = [f"{field_name}_x", f"{field_name}_y", f"{field_name}_z"]
        if all(c in columns for c in cols):
            kwargs[field_name] = tuple(
                float(row[c]) if not pd.isna(row[c]) else 0.0 for c in cols
            )

    for field_name, cols in _POSITION_COLUMNS.items():
        if all(c in columns for c in cols):
            kwargs[field_name] = tuple(
                float(row[c]) if not pd.isna(row[c]) else 0.0 for c in cols
            )

    # dtype cleanup: pandas hands back numpy scalar types; coerce the ones
    # RawState's type hints promise to be plain Python int/float/bool/str.
    for field_name in _INT_FIELDS:
        if kwargs.get(field_name) is not None:
            kwargs[field_name] = int(kwargs[field_name])
    for field_name in _FLOAT_FIELDS:
        if kwargs.get(field_name) is not None:
            kwargs[field_name] = float(kwargs[field_name])
    if kwargs.get("armed") is not None:
        kwargs["armed"] = bool(kwargs["armed"])
    if kwargs.get("flight_mode") is not None:
        kwargs["flight_mode"] = str(kwargs["flight_mode"])

    return RawState(**kwargs)


class ReplayTelemetrySource(TelemetrySource):
    """Replays a recorded telemetry file through the pipeline in original
    timestamp order, optionally sped up by `speed_multiplier` (1x, 2x, 5x,
    10x, ...). get_raw_state() blocks until it's the right (scaled) wall-
    clock time for the next row, so a 2-minute flight replayed at 1x takes
    roughly 2 minutes — the same pacing a live gateway would have."""

    def __init__(self, file_path: str | Path, speed_multiplier: float = 1.0, ring_buffer_size: int = 200):
        super().__init__(ring_buffer_size)
        self.file_path = Path(file_path)
        self.speed_multiplier = max(speed_multiplier, 0.01)

        self._rows: list[RawState] = load_telemetry_file(self.file_path)
        self._index = 0
        self._replay_wall_start: float | None = None
        self._first_ts = self._rows[0].timestamp

    @property
    def total_rows(self) -> int:
        return len(self._rows)

    @property
    def duration_s(self) -> float:
        return self._rows[-1].timestamp - self._rows[0].timestamp if len(self._rows) > 1 else 0.0

    def has_next(self) -> bool:
        return self._index < len(self._rows)

    def get_raw_state(self) -> RawState:
        if not self.has_next():
            raise StopIteration("replay exhausted — no more recorded rows")

        row = self._rows[self._index]
        self._index += 1

        if self._replay_wall_start is None:
            # First row plays immediately; every later row is paced
            # relative to this moment.
            self._replay_wall_start = time.time()
        else:
            target_elapsed = (row.timestamp - self._first_ts) / self.speed_multiplier
            actual_elapsed = time.time() - self._replay_wall_start
            sleep_for = target_elapsed - actual_elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

        return self._record(row)

    def send_test_command(self, speed_ms: float) -> Ack:
        logger.info(
            "ReplayTelemetrySource: ignoring test command (speed_ms=%.1f) — "
            "historical data, no live drone to command",
            speed_ms,
        )
        return Ack(
            timestamp=time.time(),
            accepted=True,
            latency_ms=0.0,
            detail="replay source: no live connection, command not actually sent",
        )
