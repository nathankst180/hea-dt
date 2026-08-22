"""
modules/m7_uncertainty/quantifier.py

M7 — Uncertainty Quantification (real implementation).

Loads the MAPIE conformal wrappers fitted by calibration.py and produces:

  1. Per-prediction confidence for M4's battery RUL (from the conformal
     interval's width relative to the point estimate) and M5's structural
     imbalance call (from the conformal prediction SET size).
  2. ONE fused decision_confidence — the number that actually matters for
     autonomy-gating (M10, Phase 4). This is the FUSION FORMULA the
     project's "uncertainty-gated autonomy" design principle depends on,
     spelled out here since it needs to be citable in the paper:

         decision_confidence = base_confidence * deviation_factor * data_quality_factor

         base_confidence   = min(conf_battery, conf_structural)

             MIN, not a weighted average — a single shaky prediction
             should be enough to warrant caution; averaging would let a
             confident prediction paper over an unconfident one.

         deviation_factor  = exp(-twin.deviation / config.m7.deviation_scale)

             An exponential decay in twin.deviation (M3's EKF residual).
             This is the crux of "uncertainty-gated autonomy": deviation
             is MULTIPLIED into the final score, not averaged in, so even
             a maximally confident model prediction (base_confidence=1.0)
             gets pulled down proportionally the moment the twin itself
             stops tracking reality well. At deviation=0, factor=1.0 (no
             penalty); at deviation=deviation_scale, factor≈0.37; at
             deviation=3*deviation_scale, factor≈0.05.

         data_quality_factor = clip(1 - 0.15 * len(sensor_fault_flags), 0.3, 1.0)

             M2's sensor-fault count also multiplicatively discounts
             confidence — per contracts/schemas.py's own docstring,
             decision_confidence is meant to combine model confidence,
             twin deviation, AND data validity, not deviation alone.

  Both conf_battery and conf_structural individually already reflect
  MAPIE's calibrated uncertainty — the multiplicative fusion on top is
  what turns "this one prediction looks uncertain" into "the whole
  decision this cycle should be treated cautiously."

If no calibrated conformal wrapper is found (calibration.py hasn't been
run yet), falls back to reporting full confidence per-prediction, but
STILL applies the deviation/data-quality fusion — so even in that
fallback state, the core "uncertainty-gated autonomy" behavior the tests
check for keeps working.
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

from contracts.schemas import Confidence, EnergyPlan, HealthElectrical, HealthStructural, StateVector, TwinParams
from modules.m4_electrical_rul.features import BATTERY_FEATURE_NAMES, extract_battery_features
from modules.m5_structural_shm.features import FEATURE_NAMES as STRUCTURAL_SPECTRAL_FEATURES

logger = logging.getLogger("hea_dt.m7_uncertainty")

ROTOR_CORR_NAMES = [f"rotor_corr_{i}" for i in range(4)]
STRUCTURAL_ALL_FEATURES = STRUCTURAL_SPECTRAL_FEATURES + ROTOR_CORR_NAMES

_EPS = 1e-6


@functools.lru_cache(maxsize=1)
def _load_conformal_wrappers(models_dir: str) -> Optional[tuple]:
    battery_path = Path(models_dir) / "m7_battery_rul_conformal.joblib"
    structural_path = Path(models_dir) / "m7_structural_imbalance_conformal.joblib"
    if not battery_path.exists() or not structural_path.exists():
        logger.warning(
            "M7 conformal wrappers not found in %s — run "
            "`python -m modules.m7_uncertainty.calibration` first (after M4/M5's "
            "train.py). Falling back to full per-prediction confidence; the "
            "deviation/data-quality fusion still applies.",
            models_dir,
        )
        return None
    return joblib.load(battery_path), joblib.load(structural_path)


def _battery_confidence(scr, state: StateVector, twin: TwinParams) -> float:
    features = extract_battery_features(state, twin)
    x = pd.DataFrame([features], columns=BATTERY_FEATURE_NAMES)
    points, intervals = scr.predict_interval(x)
    lower, upper = float(intervals[0, 0, 0]), float(intervals[0, 1, 0])
    point = float(points[0])
    width = max(0.0, upper - lower)
    # Narrower interval relative to the point estimate -> higher confidence.
    # Using the point estimate (not a fixed scale) to normalize means this
    # behaves sensibly whether RUL is predicted at 20s or 3000s.
    return 1.0 / (1.0 + width / (2.0 * max(point, _EPS)))


def _structural_confidence(scc, feature_row: dict) -> float:
    x = pd.DataFrame([feature_row], columns=STRUCTURAL_ALL_FEATURES)
    _, sets = scc.predict_set(x)
    set_size = int(sets[0, :, 0].sum())
    # A singleton set (exactly one class judged plausible at the target
    # confidence level) is the confident case; an empty or two-class set
    # both mean "not confidently one answer" and score the same, low,
    # regardless of WHY (MAPIE can produce either for an ambiguous input).
    return 1.0 if set_size == 1 else 0.3


def quantify(
    health_elec: HealthElectrical,
    health_struct: HealthStructural,
    energy_plan: EnergyPlan,
    twin: TwinParams,
    state: StateVector,
    config,
    structural_feature_row: Optional[dict] = None,
) -> Confidence:
    """structural_feature_row: the EXACT feature vector M5 used to produce
    health_struct this cycle — pass modules.m5_structural_shm.
    StructuralHealthPredictor.last_feature_row (see orchestrator/main.py).
    HealthStructural alone only carries M5's output scores, not the
    window-derived spectral features that produced them, and MAPIE's
    conformal classifier needs the SAME features the underlying model saw
    to give a meaningful set — not a re-derived or zeroed stand-in. If
    unavailable (e.g. a standalone test, or M5's window wasn't full yet),
    falls back to a simpler decision-boundary-distance heuristic on
    health_struct.structural_anomaly_score.
    """
    logger.info("M7 called")
    wrappers = _load_conformal_wrappers(config.paths.models_dir)

    if wrappers is None:
        conf_battery = health_elec.confidence
        conf_structural = health_struct.confidence
    else:
        battery_scr, structural_scc = wrappers
        conf_battery = _battery_confidence(battery_scr, state, twin)

        if structural_feature_row is not None:
            conf_structural = _structural_confidence(structural_scc, structural_feature_row)
        else:
            # Fallback: distance from the 0.5 decision boundary, 0-1.
            conf_structural = abs(health_struct.structural_anomaly_score - 0.5) * 2.0

    per_prediction = {
        "battery_rul": conf_battery,
        "structural_anomaly": conf_structural,
        "energy_margin": 1.0,  # M6 is deterministic physics — no uncertainty model
    }

    base_confidence = min(conf_battery, conf_structural)
    deviation_factor = math.exp(-twin.deviation / config.m7.deviation_scale)
    data_quality_factor = max(0.3, min(1.0, 1.0 - 0.15 * len(state.sensor_fault_flags)))
    decision_confidence = base_confidence * deviation_factor * data_quality_factor

    return Confidence(
        timestamp=time.time(),
        per_prediction=per_prediction,
        decision_confidence=decision_confidence,
    )
