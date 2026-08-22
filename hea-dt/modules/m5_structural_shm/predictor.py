"""
modules/m5_structural_shm/predictor.py

M5 — Structural Health Monitoring, vibration-based (real implementation).

Stateful (unlike M4/M6/M7): spectral analysis needs a WINDOW of recent
accel/gyro samples, not just this cycle's reading, so
StructuralHealthPredictor keeps its own rolling buffer across calls — the
same pattern modules/m2_validation and modules/m3_twin_state use, and for
the same reason (a single reading in isolation isn't enough).

Loads the RandomForest models trained by train.py (see that file and
features.py for the full design — including the important sample-rate
caveat: this operates on per-cycle telemetry, not raw high-rate IMU, so
the "rotor harmonic" it detects is a stylized low-frequency proxy, not the
real ~80Hz+ frequency a physical propeller imbalance would show).

If no trained model is found, falls back to the Phase 0 stub's "no
anomaly detected" behavior with a one-time warning.
"""

from __future__ import annotations

import functools
import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd

from contracts.schemas import HealthStructural, StateVector, TwinParams
from modules.m5_structural_shm.features import FEATURE_NAMES, extract_spectral_features, per_rotor_correlation

logger = logging.getLogger("hea_dt.m5_structural_shm")

ROTOR_CORR_NAMES = [f"rotor_corr_{i}" for i in range(4)]
ALL_FEATURE_NAMES = FEATURE_NAMES + ROTOR_CORR_NAMES

WINDOW_SIZE = 32


@functools.lru_cache(maxsize=1)
def _load_models(models_dir: str) -> Optional[tuple]:
    clf_path = Path(models_dir) / "m5_imbalance_classifier.joblib"
    reg_path = Path(models_dir) / "m5_damage_regressor.joblib"
    if not clf_path.exists() or not reg_path.exists():
        logger.warning(
            "M5 models not found in %s — run `python -m modules.m5_structural_shm.train` "
            "first. Falling back to no-anomaly stub predictions.",
            models_dir,
        )
        return None
    return joblib.load(clf_path), joblib.load(reg_path)


class StructuralHealthPredictor:
    """Holds a rolling window of recent accel/gyro/motor-rpm samples and
    predicts HealthStructural from it each cycle."""

    def __init__(self, config, window_size: int = WINDOW_SIZE):
        self.config = config
        self.window_size = window_size
        self._accel: deque = deque(maxlen=window_size)
        self._gyro: deque = deque(maxlen=window_size)
        self._timestamps: deque = deque(maxlen=window_size)
        self._motor_rpm: deque = deque(maxlen=window_size)
        self._models = _load_models(config.paths.models_dir)

        self.last_feature_row: Optional[dict] = None
        """The exact feature vector used to produce the most recent
        prediction — M7 (Phase 3) needs this to re-run MAPIE's conformal
        wrapper on the SAME features this classifier saw, since
        HealthStructural itself only carries the output scores, not the
        window-derived spectral features that produced them."""

    def predict(self, state: StateVector, twin: TwinParams) -> HealthStructural:
        logger.info("M5 called")
        raw = state.raw
        n_rotors = len(raw.motor_rpm) or 4

        self._accel.append(raw.accel_xyz)
        self._gyro.append(raw.gyro_xyz)
        self._timestamps.append(raw.timestamp)
        self._motor_rpm.append(list(raw.motor_rpm))

        spectral = extract_spectral_features(list(self._accel), list(self._gyro), list(self._timestamps))
        rotor_scores = per_rotor_correlation(list(self._accel), list(self._motor_rpm))
        rotor_scores = (rotor_scores + [0.0] * n_rotors)[:n_rotors]

        feature_row = dict(spectral)
        for i in range(4):
            feature_row[f"rotor_corr_{i}"] = rotor_scores[i] if i < len(rotor_scores) else 0.0
        self.last_feature_row = feature_row

        if self._models is None:
            return HealthStructural(
                timestamp=time.time(),
                imbalance_flag=False,
                prop_damage_score=0.0,
                structural_anomaly_score=0.0,
                per_rotor_scores=rotor_scores,
                confidence=1.0,
            )

        clf, reg = self._models
        x = pd.DataFrame([feature_row], columns=ALL_FEATURE_NAMES)

        anomaly_score = float(clf.predict_proba(x)[0, 1])
        damage_score = float(max(0.0, min(1.0, reg.predict(x)[0])))
        imbalance_flag = anomaly_score > self.config.thresholds.structural_imbalance_threshold

        return HealthStructural(
            timestamp=time.time(),
            imbalance_flag=imbalance_flag,
            prop_damage_score=damage_score,
            structural_anomaly_score=anomaly_score,
            per_rotor_scores=rotor_scores,
            confidence=1.0,  # refined into decision_confidence by M7
        )
