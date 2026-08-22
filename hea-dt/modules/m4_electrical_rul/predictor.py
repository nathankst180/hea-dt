"""
modules/m4_electrical_rul/predictor.py

M4 — Electrical & Powertrain RUL Prediction (real implementation).

Loads the LightGBM models trained by train.py (from config.paths.
models_dir) and predicts HealthElectrical from (StateVector, TwinParams):

  - battery_rul_s: a LightGBM regressor over BATTERY_FEATURE_NAMES
    (features.py), trained on data/battery_dataset.parquet.
  - battery_soh_pct: NOT a trained model — derived directly from
    twin.battery_internal_resistance_ohm via the same closed-form formula
    data/generate_fault_data.py uses for its ground truth (see there for
    R_FAIL_OHM/NOMINAL_R_OHM). Internal resistance essentially IS what
    "state of health" means for a battery; a second model trained to
    reproduce a known deterministic function would add nothing.
  - motor_health / esc_health: 1 - P(weak) from the LightGBM classifier,
    per motor — a confidently-weak motor scores near 0, a healthy one
    near 1, matching HealthElectrical's stated 0-1 convention.
  - p_fail_horizon: P(powertrain failure within config.thresholds.
    p_fail_horizon_s — the configurable "N"), derived ANALYTICALLY from
    battery_rul_s via a constant-hazard-rate survival transform:

        P_batt = 1 - exp(-horizon_s / max(rul_s, epsilon))

    combined with the weakest motor's own failure probability (mirroring
    the same transform against a fixed nominal motor lifetime), treating
    the two risks as roughly independent:

        P_total = 1 - (1 - P_batt) * (1 - P_motor)

    This is a standard, explainable transform, chosen over training a
    THIRD model for "will it fail within N minutes" specifically because
    N is configurable: an analytic function of the RUL prediction adapts
    to any horizon without retraining, where a classifier trained for one
    fixed N would need retraining every time N changes.

If no trained model is found (train.py hasn't been run yet), falls back
to the Phase 0 stub's "fully healthy" behavior with a one-time warning —
so --mock runs and tests that don't need real predictions keep working.
"""

from __future__ import annotations

import functools
import logging
import math
import time
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd

from contracts.schemas import HealthElectrical, StateVector, TwinParams
from modules.m4_electrical_rul.features import (
    BATTERY_FEATURE_NAMES,
    MOTOR_FEATURE_NAMES,
    extract_battery_features,
    extract_motor_features,
)

logger = logging.getLogger("hea_dt.m4_electrical_rul")

R_FAIL_OHM = 0.15
"""Must match data/generate_fault_data.py's R_FAIL_OHM — the threshold
battery_soh_pct is derived against."""
NOMINAL_R_OHM = 0.05
"""Must match modules/m3_twin_state/estimator.py's initial guess and
data/generate_fault_data.py's NOMINAL_R_OHM."""

_NOMINAL_MOTOR_LIFETIME_S = 3600.0
"""Reference lifetime used to turn a motor's health score into a rough
failure probability for the p_fail_horizon transform (see module docstring)."""

_EPS = 1e-6


@functools.lru_cache(maxsize=1)
def _load_models(models_dir: str) -> Optional[tuple]:
    battery_path = Path(models_dir) / "m4_battery_rul.joblib"
    motor_path = Path(models_dir) / "m4_motor_weak.joblib"
    if not battery_path.exists() or not motor_path.exists():
        logger.warning(
            "M4 models not found in %s — run `python -m modules.m4_electrical_rul.train` "
            "first. Falling back to fully-healthy stub predictions.",
            models_dir,
        )
        return None
    return joblib.load(battery_path), joblib.load(motor_path)


def _battery_soh_pct(r_batt_ohm: float) -> float:
    """Deterministic function of internal resistance — see module docstring."""
    return 100.0 * max(0.0, min(1.0, (R_FAIL_OHM - r_batt_ohm) / (R_FAIL_OHM - NOMINAL_R_OHM)))


def _p_fail_from_rul(rul_s: float, horizon_s: float) -> float:
    return 1.0 - math.exp(-horizon_s / max(rul_s, _EPS))


def predict(state: StateVector, twin: TwinParams, config) -> HealthElectrical:
    logger.info("M4 called")
    models = _load_models(config.paths.models_dir)
    n_motors = len(state.raw.motor_rpm) or len(twin.motor_thrust_efficiency) or 4

    if models is None:
        return HealthElectrical(
            timestamp=time.time(),
            battery_soh_pct=100.0,
            battery_rul_s=float("inf"),
            motor_health=[1.0] * n_motors,
            esc_health=[1.0] * n_motors,
            p_fail_horizon=0.0,
            confidence=1.0,
        )

    battery_model, motor_model = models
    horizon_s = config.thresholds.p_fail_horizon_s

    battery_features = extract_battery_features(state, twin)
    battery_x = pd.DataFrame([battery_features], columns=BATTERY_FEATURE_NAMES)
    battery_rul_s = float(battery_model.predict(battery_x)[0])
    battery_rul_s = max(0.0, battery_rul_s)
    battery_soh_pct = _battery_soh_pct(twin.battery_internal_resistance_ohm)

    motor_rows = extract_motor_features(state, twin)
    if motor_rows:
        motor_x = pd.DataFrame(motor_rows, columns=MOTOR_FEATURE_NAMES)
        motor_weak_proba = motor_model.predict_proba(motor_x)[:, 1]
        motor_health = [float(1.0 - p) for p in motor_weak_proba]
    else:
        motor_weak_proba = [0.0] * n_motors
        motor_health = [1.0] * n_motors
    esc_health = list(motor_health)  # no independent ESC-only signal yet; ESC and motor share the same current/temp features

    p_fail_batt = _p_fail_from_rul(battery_rul_s, horizon_s)
    worst_motor_health = min(motor_health) if motor_health else 1.0
    p_fail_motor = _p_fail_from_rul(worst_motor_health * _NOMINAL_MOTOR_LIFETIME_S, horizon_s)
    p_fail_horizon = 1.0 - (1.0 - p_fail_batt) * (1.0 - p_fail_motor)

    return HealthElectrical(
        timestamp=time.time(),
        battery_soh_pct=battery_soh_pct,
        battery_rul_s=battery_rul_s,
        motor_health=motor_health,
        esc_health=esc_health,
        p_fail_horizon=p_fail_horizon,
        confidence=1.0,  # refined into decision_confidence by M7, which has the real uncertainty model
    )
