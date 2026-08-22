"""
modules/m5_structural_shm/features.py

Feature extraction for M5 — Structural Health Monitoring.

Pure, stateless functions shared between TRAINING
(data/generate_fault_data.py) and INFERENCE (modules/m5_structural_shm/
predictor.py), so the trained model always sees exactly the feature
representation it was trained on — no train/inference skew.

IMPORTANT SIMPLIFICATION, stated plainly rather than hidden: real
propeller-imbalance detection looks for vibration energy at the rotor's
harmonic frequency, which for a ~5000 RPM motor is ~83 Hz and needs IMU
sampling well above that (Nyquist) — hundreds of Hz to kHz. Our telemetry
contract (RawState) represents ONE reading per pipeline cycle, at
whatever rate the loop runs (1-10 Hz in this prototype), not a buffered
high-rate IMU stream. At that rate we CANNOT resolve a real ~83 Hz rotor
harmonic. This module therefore treats an injected "imbalance" as a much
lower, STYLIZED oscillation (~1-3 Hz) superimposed on the per-cycle
vibration summary — a scaled-down PROXY for the real phenomenon, chosen
so the mechanism itself (spectral peak detection, harmonic-band energy,
motor-command correlation) is genuine and demonstrable at the sample
rates this prototype actually operates at. A production implementation
would tap RAW_IMU/HIGHRES_IMU at its native high rate directly,
independent of the per-cycle StateVector loop, and analyze true
rotor-harmonic frequencies. This belongs in the paper's limitations
section — see also README.md's Layer B notes.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import fft as sp_fft

MIN_WINDOW = 6
"""Below this many samples, a spectrum isn't meaningful — features fall back to 0.0."""

FEATURE_NAMES = [
    "accel_peak_freq_hz",
    "accel_peak_magnitude",
    "accel_peak_energy_ratio",
    "accel_total_energy",
    "gyro_peak_freq_hz",
    "gyro_peak_magnitude",
    "gyro_total_energy",
    "accel_std",
    "gyro_std",
]
"""Fixed column order — training and inference must agree on this exactly."""


def _magnitude_series(samples: list[tuple[float, float, float]]) -> np.ndarray:
    arr = np.asarray(samples, dtype=float)
    return np.sqrt((arr**2).sum(axis=1))


def _spectrum(signal: np.ndarray, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """Real FFT magnitude spectrum of a mean-removed 1D signal.
    Returns (frequencies_hz, magnitudes), excluding the DC (0 Hz) bin."""
    centered = signal - signal.mean()
    n = len(centered)
    magnitudes = np.abs(sp_fft.rfft(centered)) / n
    freqs = sp_fft.rfftfreq(n, d=1.0 / sample_rate_hz)
    return freqs[1:], magnitudes[1:]  # drop DC bin


def _peak_features(freqs: np.ndarray, magnitudes: np.ndarray) -> tuple[float, float, float, float]:
    """Returns (peak_freq_hz, peak_magnitude, peak_energy_ratio, total_energy)."""
    total_energy = float(np.sum(magnitudes**2))
    if len(magnitudes) == 0 or total_energy <= 1e-12:
        return 0.0, 0.0, 0.0, 0.0
    peak_idx = int(np.argmax(magnitudes))
    peak_freq = float(freqs[peak_idx])
    peak_mag = float(magnitudes[peak_idx])
    peak_energy_ratio = float(magnitudes[peak_idx] ** 2 / total_energy)
    return peak_freq, peak_mag, peak_energy_ratio, total_energy


def extract_spectral_features(
    accel_samples: list[tuple[float, float, float]],
    gyro_samples: list[tuple[float, float, float]],
    timestamps: list[float],
) -> dict[str, float]:
    """Compute FFT-based vibration features from a window of recent
    accel/gyro readings (oldest first). All three lists must be the same
    length and time-aligned. Returns a flat dict keyed by FEATURE_NAMES,
    always present — defaulting to 0.0 if the window is too short
    (MIN_WINDOW) or the timestamps aren't usable for a sample rate.
    """
    n = len(timestamps)
    if n < MIN_WINDOW or len(accel_samples) != n or len(gyro_samples) != n:
        return {name: 0.0 for name in FEATURE_NAMES}

    dts = np.diff(timestamps)
    dts = dts[dts > 0]
    if len(dts) == 0:
        return {name: 0.0 for name in FEATURE_NAMES}
    sample_rate_hz = 1.0 / float(np.mean(dts))

    accel_mag = _magnitude_series(accel_samples)
    gyro_mag = _magnitude_series(gyro_samples)

    a_freqs, a_spec = _spectrum(accel_mag, sample_rate_hz)
    g_freqs, g_spec = _spectrum(gyro_mag, sample_rate_hz)

    a_peak_freq, a_peak_mag, a_peak_ratio, a_total = _peak_features(a_freqs, a_spec)
    g_peak_freq, g_peak_mag, g_peak_ratio, g_total = _peak_features(g_freqs, g_spec)

    return {
        "accel_peak_freq_hz": a_peak_freq,
        "accel_peak_magnitude": a_peak_mag,
        "accel_peak_energy_ratio": a_peak_ratio,
        "accel_total_energy": a_total,
        "gyro_peak_freq_hz": g_peak_freq,
        "gyro_peak_magnitude": g_peak_mag,
        "gyro_total_energy": g_total,
        "accel_std": float(np.std(accel_mag)),
        "gyro_std": float(np.std(gyro_mag)),
    }


def per_rotor_correlation(
    accel_samples: list[tuple[float, float, float]],
    motor_rpm_samples: list[list[float]],
) -> list[float]:
    """Correlate vibration magnitude against each motor's RPM deviation
    from the window's per-motor mean — a proxy for "does this motor's
    behavior track the vibration", approximating the design doc's
    "correlation of vibration power to motor commands to estimate
    imbalance magnitude" without needing a per-motor accelerometer.

    Returns one score per motor in [0, 1] (absolute correlation
    coefficient — sign doesn't matter, only strength of association).
    motor_rpm_samples[i] is the RPM list for timestep i (one entry per
    motor); all timesteps must report the same motor count.
    """
    n = len(accel_samples)
    if n < MIN_WINDOW or len(motor_rpm_samples) != n:
        return []

    n_motors = len(motor_rpm_samples[0]) if motor_rpm_samples[0] else 0
    if n_motors == 0:
        return []

    accel_mag = _magnitude_series(accel_samples)
    scores = []
    for m in range(n_motors):
        rpm_series = np.array([row[m] if m < len(row) else 0.0 for row in motor_rpm_samples])
        if np.std(rpm_series) < 1e-9 or np.std(accel_mag) < 1e-9:
            scores.append(0.0)
            continue
        corr = np.corrcoef(rpm_series, accel_mag)[0, 1]
        scores.append(0.0 if math.isnan(corr) else float(abs(corr)))
    return scores
