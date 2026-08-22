"""
modules/m2_validation/validator.py

M2 — Data Validation & Sensor-Fault Isolation.

Cleans one cycle's RawState into a StateVector, and — the module's
central job — tells a FAILING SENSOR apart from a FAILING COMPONENT
before any prediction module (M4/M5) ever sees the data. See the
docstrings on SensorValidator's check methods below for exactly how that
distinction is made; the short version is:

  SENSOR fault  = an isolated, single-cycle, physically-implausible or
                  discontinuous reading that doesn't fit the recent trend
                  or the rest of this cycle's readings.
  COMPONENT problem = a persistent, gradually-developing trend across
                  many consecutive cycles — which we deliberately do NOT
                  flag as a sensor fault, because that's real signal M4/M5
                  need to see, not noise to hide.
"""

from __future__ import annotations

import functools
import logging
import math
from collections import deque
from dataclasses import replace

import numpy as np
from sklearn.ensemble import IsolationForest

from contracts.schemas import RawState, StateVector

logger = logging.getLogger("hea_dt.m2_validation")

# Hard physical bounds. A reading outside these is not a possible real
# aircraft state — it's corrupted telemetry or a broken sensor, full stop.
_RANGE_CHECKS: dict[str, tuple[float, float]] = {
    "battery_voltage_v": (6.0, 25.2),  # below empty-cell / above max charge voltage for a typical 3-6S pack
    "battery_current_a": (0.0, 80.0),
    "battery_temp_c": (-20.0, 85.0),
    "battery_remaining_pct": (0.0, 100.0),
    "gps_satellites_visible": (0, 32),
}

# Fields we track a "last known good" value for, so a gap (None) can be
# imputed rather than propagated as a hole downstream.
_IMPUTABLE_FIELDS = (
    "battery_voltage_v",
    "battery_current_a",
    "battery_remaining_pct",
    "battery_temp_c",
    "airspeed_ms",
    "groundspeed_ms",
)


@functools.lru_cache(maxsize=1)
def _fit_baseline_isolation_forest() -> IsolationForest:
    """Fit an IsolationForest on synthetic nominal samples spanning the
    plausible operating envelope.

    We don't have real logged flight data yet (that comes from Phase 2's
    fault-injection dataset generation), so this gives M2 something
    sensible to compare against from cycle 1. Once real/simulated flight
    logs exist, refit this on them instead (see training/).

    Cached at MODULE level (not per SensorValidator instance): the
    training data is fully deterministic (fixed seed), so every instance
    would fit an IDENTICAL model — SensorValidator is constructed fresh
    per replay session (api/replay_session.py) and, now, once per flight
    during dataset generation (data/generate_fault_data.py, potentially
    hundreds of times per run), so refitting an ensemble that's always
    going to come out the same was pure waste. Measured ~180ms per fit at
    50 trees; this cache makes every construction after the first ~free.
    """
    rng = np.random.default_rng(0)
    n = 400
    battery_v = rng.uniform(13.5, 16.8, n)
    battery_i = rng.uniform(5.0, 20.0, n)
    accel_norm = rng.normal(9.81, 0.3, n)
    gyro_norm = np.abs(rng.normal(0.0, 0.03, n))
    features = np.column_stack([battery_v, battery_i, accel_norm, gyro_norm])
    # n_estimators=50 (not sklearn's default 100) roughly halves fit time
    # on this 400-sample/4-feature synthetic baseline with no measurable
    # loss of anomaly-detection quality; n_jobs=-1 is NOT faster here,
    # since multiprocessing startup overhead dominates on data this small.
    model = IsolationForest(n_estimators=50, contamination=0.05, random_state=0, n_jobs=1)
    model.fit(features)
    return model


class SensorValidator:
    """Stateful M2 validator: holds a short rolling history so it can spot
    gaps, discontinuities, and cross-sensor disagreement — a single
    RawState in isolation isn't enough for any of those checks."""

    def __init__(self, config, history_size: int = 10):
        self.config = config
        self._history: deque[RawState] = deque(maxlen=history_size)
        self._last_good: dict[str, float] = {}
        self._iso_forest = _fit_baseline_isolation_forest()

    def validate(self, raw: RawState) -> StateVector:
        cleaned = replace(raw)  # shallow copy so we never mutate the caller's RawState
        validity: dict[str, bool] = {}
        signal_sigma: dict[str, float] = {}
        sensor_fault_flags: list[str] = []

        self._check_ranges(cleaned, validity, sensor_fault_flags)
        self._impute_gaps(cleaned, validity)
        self._check_gps_fix_quality(cleaned, validity, sensor_fault_flags)
        self._check_gps_imu_consistency(cleaned, validity, sensor_fault_flags)
        self._check_anomaly(cleaned, validity, sensor_fault_flags)
        self._compute_sigma(signal_sigma)

        self._update_history(cleaned, validity)

        # Two checks (GPS fix quality, GPS/IMU consistency) can both flag
        # the same GPS fields in the same cycle; dedupe while preserving
        # the order they were first flagged in.
        sensor_fault_flags = list(dict.fromkeys(sensor_fault_flags))

        return StateVector(
            raw=cleaned,
            validity=validity,
            signal_sigma=signal_sigma,
            sensor_fault_flags=sensor_fault_flags,
        )

    # ---- individual checks -----------------------------------------------

    def _check_ranges(self, raw: RawState, validity: dict, sensor_fault_flags: list) -> None:
        """Hard plausibility bounds. Anything outside these is treated as a
        SENSOR fault (never a real aircraft state) and clamped back to the
        last known-good value so it can't corrupt a downstream prediction."""
        for field_name, (lo, hi) in _RANGE_CHECKS.items():
            value = getattr(raw, field_name)
            if value is None:
                continue  # handled by _impute_gaps
            ok = lo <= value <= hi
            validity[field_name] = ok
            if not ok:
                sensor_fault_flags.append(field_name)
                setattr(raw, field_name, self._last_good.get(field_name, value))

    def _impute_gaps(self, raw: RawState, validity: dict) -> None:
        """A None reading is a dropped/missing message, not a value to
        judge as valid/invalid — fill it from the last known-good value and
        tag that it was imputed, so downstream modules can still discount
        it if they care to."""
        for field_name in _IMPUTABLE_FIELDS:
            value = getattr(raw, field_name)
            if value is None:
                imputed = self._last_good.get(field_name)
                if imputed is not None:
                    setattr(raw, field_name, imputed)
                validity[f"{field_name}_imputed"] = imputed is not None

    def _check_gps_fix_quality(self, raw: RawState, validity: dict, sensor_fault_flags: list) -> None:
        """A GPS fix_type below 2 (no fix / no 3D fix) means the receiver
        itself has lost lock — there is no aircraft component that can
        cause that, so it's always a SENSOR fault. This is a distinct
        check from the range check on gps_satellites_visible: 0 satellites
        is numerically "in range" (0-32), so without this check a total
        fix loss would silently pass through unflagged. A momentary loss
        of lock (multipath, urban canyon, simulated dropout) is common and
        transient; any downstream module should treat position/velocity as
        untrustworthy for this cycle rather than trusting stale numbers.
        """
        fix_ok = raw.gps_fix_type >= 2
        validity["gps_fix_type"] = fix_ok
        if not fix_ok:
            sensor_fault_flags.append("gps_fix_type")
            sensor_fault_flags.append("groundspeed_ms")

    def _check_gps_imu_consistency(self, raw: RawState, validity: dict, sensor_fault_flags: list) -> None:
        """Cross-sensor consistency: compare this cycle's GPS-derived
        ground speed against the previous cycle's speed plus the horizontal
        acceleration integrated over the elapsed time (a crude "IMU-
        integrated speed"). Large disagreement points at a SENSOR problem
        (most often a transient GPS glitch), not a real component problem —
        there's no powertrain component that could make GPS and IMU
        disagree about speed like this — so we flag the GPS fields, not a
        health/component field.

        Simplification, noted here rather than buried: this ignores
        attitude, so it treats body-frame horizontal acceleration as if it
        were already in the world frame. A full implementation would rotate
        accel_xyz into the NED frame using the attitude quaternion first.
        For a fast plausibility check this coarse version is enough.
        """
        threshold_ms = self.config.thresholds.gps_imu_disagreement_ms
        if not self._history:
            validity["gps_imu_consistency"] = True
            return

        prev = self._history[-1]
        dt = raw.timestamp - prev.timestamp
        if dt <= 0 or dt > 5.0:
            validity["gps_imu_consistency"] = True
            return

        horiz_accel = math.hypot(raw.accel_xyz[0], raw.accel_xyz[1])
        prev_speed = math.hypot(prev.velocity_ned[0], prev.velocity_ned[1])
        imu_integrated_speed = prev_speed + horiz_accel * dt

        gps_speed = raw.groundspeed_ms
        if gps_speed is None:
            gps_speed = math.hypot(raw.velocity_ned[0], raw.velocity_ned[1])

        disagreement = abs(imu_integrated_speed - gps_speed)
        consistent = disagreement < threshold_ms
        validity["gps_imu_consistency"] = consistent
        if not consistent:
            sensor_fault_flags.append("groundspeed_ms")
            sensor_fault_flags.append("gps_fix_type")

    def _check_anomaly(self, raw: RawState, validity: dict, sensor_fault_flags: list) -> None:
        """IsolationForest flags this cycle as unusual in joint feature
        space. We then decide sensor-fault vs. component-problem using
        recent history: a sudden discontinuity relative to a stable recent
        past is a SENSOR glitch (flagged); a value that continues a trend
        already visible over the last few cycles is left unflagged so
        M4/M5 still get to see what may be genuine developing degradation.
        """
        accel_norm = math.sqrt(sum(a * a for a in raw.accel_xyz))
        gyro_norm = math.sqrt(sum(g * g for g in raw.gyro_xyz))
        features = np.array(
            [
                [
                    raw.battery_voltage_v if raw.battery_voltage_v is not None else 0.0,
                    raw.battery_current_a if raw.battery_current_a is not None else 0.0,
                    accel_norm,
                    gyro_norm,
                ]
            ]
        )
        is_outlier = self._iso_forest.predict(features)[0] == -1
        validity["isolation_forest"] = not is_outlier

        if is_outlier and self._is_discontinuous_jump("battery_voltage_v", raw.battery_voltage_v):
            sensor_fault_flags.append("battery_voltage_v")

    def _is_discontinuous_jump(self, field_name: str, value, z_threshold: float = 4.0, min_history: int = 3) -> bool:
        if value is None or len(self._history) < min_history:
            return False
        recent = [getattr(h, field_name) for h in self._history if getattr(h, field_name) is not None]
        if len(recent) < min_history:
            return False
        mean = sum(recent) / len(recent)
        variance = sum((v - mean) ** 2 for v in recent) / len(recent)
        std = math.sqrt(variance)
        z = abs(value - mean) / max(std, 1e-6)
        return z > z_threshold

    def _compute_sigma(self, signal_sigma: dict) -> None:
        for field_name in ("battery_voltage_v", "battery_current_a"):
            recent = [getattr(h, field_name) for h in self._history if getattr(h, field_name) is not None]
            if len(recent) >= 2:
                mean = sum(recent) / len(recent)
                variance = sum((v - mean) ** 2 for v in recent) / len(recent)
                signal_sigma[field_name] = math.sqrt(variance)

    def _update_history(self, raw: RawState, validity: dict) -> None:
        self._history.append(raw)
        for field_name in _IMPUTABLE_FIELDS:
            value = getattr(raw, field_name)
            if value is not None and validity.get(field_name, True):
                self._last_good[field_name] = value
