"""
modules/m7_uncertainty/calibration.py

Fits MAPIE conformal wrappers around M4's ALREADY-TRAINED battery RUL
regressor and M5's ALREADY-TRAINED imbalance classifier — `prefit=True`,
so MAPIE conformalizes around a fixed base model rather than fitting its
own. Calibrated on the VAL split, which was never used to fit those base
models (see modules/m4_electrical_rul/train.py and modules/
m5_structural_shm/train.py — both train only on the TRAIN split).
Empirical coverage is then checked on the TEST split, which neither the
base models nor the conformal calibration have seen — the number that
actually tells you whether the intervals mean what they claim.

Run (after M4's and M5's train.py have both been run):
    python -m modules.m7_uncertainty.calibration
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from mapie.classification import SplitConformalClassifier
from mapie.regression import SplitConformalRegressor

from config.loader import load_config
from modules.m4_electrical_rul.features import BATTERY_FEATURE_NAMES
from modules.m5_structural_shm.features import FEATURE_NAMES as STRUCTURAL_SPECTRAL_FEATURES

ROTOR_CORR_NAMES = [f"rotor_corr_{i}" for i in range(4)]
STRUCTURAL_ALL_FEATURES = STRUCTURAL_SPECTRAL_FEATURES + ROTOR_CORR_NAMES


def _split(df: pd.DataFrame, name: str) -> pd.DataFrame:
    return df[df["split"] == name]


def calibrate_battery_rul(config, models_dir: Path, data_dir: Path) -> dict:
    battery_model = joblib.load(models_dir / "m4_battery_rul.joblib")
    df = pd.read_parquet(data_dir / "battery_dataset.parquet")
    val, test = _split(df, "val"), _split(df, "test")

    scr = SplitConformalRegressor(estimator=battery_model, confidence_level=config.m7.confidence_level, prefit=True)
    scr.conformalize(val[BATTERY_FEATURE_NAMES], val["battery_rul_s_true"])

    _, intervals = scr.predict_interval(test[BATTERY_FEATURE_NAMES])
    lower, upper = intervals[:, 0, 0], intervals[:, 1, 0]
    y_test = test["battery_rul_s_true"].to_numpy()
    coverage = float(np.mean((y_test >= lower) & (y_test <= upper)))
    mean_width = float(np.mean(upper - lower))

    joblib.dump(scr, models_dir / "m7_battery_rul_conformal.joblib")
    return {
        "battery_rul_target_coverage": config.m7.confidence_level,
        "battery_rul_empirical_coverage_test": coverage,
        "battery_rul_mean_interval_width_s_test": mean_width,
    }


def calibrate_structural_imbalance(config, models_dir: Path, data_dir: Path) -> dict:
    clf = joblib.load(models_dir / "m5_imbalance_classifier.joblib")
    df = pd.read_parquet(data_dir / "structural_dataset.parquet")
    val, test = _split(df, "val"), _split(df, "test")

    scc = SplitConformalClassifier(estimator=clf, confidence_level=config.m7.confidence_level, prefit=True)
    scc.conformalize(val[STRUCTURAL_ALL_FEATURES], val["imbalance_true"])

    _, sets = scc.predict_set(test[STRUCTURAL_ALL_FEATURES])  # sets: (n, n_classes, 1) boolean membership
    y_test = test["imbalance_true"].to_numpy()
    covered = sets[np.arange(len(y_test)), y_test, 0]  # was the true class inside the predicted set?
    coverage = float(np.mean(covered))
    mean_set_size = float(np.mean(sets[:, :, 0].sum(axis=1)))

    joblib.dump(scc, models_dir / "m7_structural_imbalance_conformal.joblib")
    return {
        "structural_imbalance_target_coverage": config.m7.confidence_level,
        "structural_imbalance_empirical_coverage_test": coverage,
        "structural_imbalance_mean_set_size_test": mean_set_size,
    }


def main() -> None:
    config = load_config("config/config.yaml")
    models_dir = Path(config.paths.models_dir)
    data_dir = Path(config.paths.data_dir)
    results_dir = Path("results")
    results_dir.mkdir(parents=True, exist_ok=True)

    metrics = {}
    metrics.update(calibrate_battery_rul(config, models_dir, data_dir))
    metrics.update(calibrate_structural_imbalance(config, models_dir, data_dir))

    print(json.dumps(metrics, indent=2))
    metrics_path = results_dir / "m7_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\nSaved conformal wrappers to {models_dir}/")
    print(f"Saved metrics to {metrics_path}")


if __name__ == "__main__":
    main()
