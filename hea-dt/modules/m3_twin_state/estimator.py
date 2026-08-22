"""
modules/m3_twin_state/estimator.py

M3 — Digital-Twin State Estimation & Self-Calibration.

Runs a lightweight Extended Kalman Filter (EKF, via filterpy) that treats
three physical parameters as slowly-drifting unknowns and continuously
re-estimates them from telemetry:

  STATE  x = [mass_kg, battery_internal_resistance_ohm, thrust_efficiency]

    mass_kg                     total aircraft mass including payload
    battery_internal_resistance_ohm   R in V = V_oc(charge%) - I * R
    thrust_efficiency           dimensionless global scale (~1.0 nominal)
                                 on the simple thrust model
                                 F = K_THRUST * thrust_efficiency * sum(rpm_i^2)

  We use a random-walk process model (F = identity, small process noise Q)
  rather than modeling how these parameters change over time, because we
  have no mechanistic model of WHY they drift (wear, payload changes,
  etc.). This is standard practice for EKF-based PARAMETER estimation (as
  opposed to estimating a fast-moving state like position), and matches
  the design doc's "online estimation of drifting parameters."

  MEASUREMENTS z = [battery_voltage_v, vertical_specific_force]

    z1 = battery_voltage_v                    ~ h1(x) = V_oc(remaining_pct) - I * R
    z2 = accel_xyz[2] (vertical/body-z accel) ~ h2(x) = F(thrust_efficiency) / mass

  DEVIATION delta(t): the normalized innovation (measurement residual)
  after this cycle's update, i.e. how far off the twin's prediction was
  BEFORE correcting itself. A twin that's tracking reality well has a
  small, stable delta(t); a twin whose physical assumptions no longer
  match the real aircraft (a genuinely failing battery, a damaged motor)
  will show a persistently large delta(t) even after the filter has had
  time to converge — that's the signal downstream modules (M8, M10) use
  to become more conservative.

  Two deliberate simplifications, called out here rather than hidden:
    1. h2 treats body-frame vertical accel as if it were world-frame
       vertical accel (ignores attitude rotation) — fine near-hover,
       increasingly wrong at high roll/pitch.
    2. V_oc(remaining_pct) is a linear interpolation between empty/full
       pack voltage, not a real nonlinear LiPo discharge curve.
  Both are acceptable for a "lightweight" twin per the project's design
  principles, and are easy to replace once real flight data is available.
"""

from __future__ import annotations

import logging

import numpy as np
from filterpy.kalman import ExtendedKalmanFilter

from contracts.schemas import StateVector, TwinParams

logger = logging.getLogger("hea_dt.m3_twin_state")


class TwinStateEstimator:
    G_M_S2 = 9.80665

    K_THRUST = 1.5e-7
    """Thrust constant in F = K_THRUST * efficiency * sum(rpm^2). Chosen so
    that ~4 motors at a plausible hover RPM (~5000) with efficiency=1.0
    produce roughly the weight of a ~1.5kg quad; replace with a fitted
    value once real thrust-stand or SITL data is available."""

    V_EMPTY, V_FULL = 13.2, 16.8
    """Linear open-circuit-voltage approximation, 0% and 100% remaining."""

    NOMINAL_HOVER_RPM = 5000.0
    """Fallback used only when motor_rpm telemetry is unavailable (e.g. no
    ESC_STATUS messages), so a missing signal doesn't masquerade as "zero
    thrust" and spuriously blow up the deviation metric."""

    def __init__(self, config):
        self.config = config

        self.ekf = ExtendedKalmanFilter(dim_x=3, dim_z=2)
        self.ekf.x = np.array([[1.5], [0.05], [1.0]])  # initial guess: 1.5kg, 0.05 ohm, efficiency=1.0
        self.ekf.P = np.diag([0.25, 0.01, 0.05])  # initial uncertainty (fairly loose)
        self.ekf.Q = np.diag([1e-5, 1e-6, 1e-5])  # process noise: parameters drift slowly
        self.ekf.R = np.diag([0.05, 0.02])  # measurement noise: voltage ~V^2, accel ~(m/s^2)^2

    def estimate(self, state: StateVector) -> TwinParams:
        raw = state.raw
        n_motors = len(raw.motor_rpm) or len(raw.motor_pwm) or 4
        rpm = raw.motor_rpm if raw.motor_rpm else [self.NOMINAL_HOVER_RPM] * n_motors
        sum_rpm_sq = sum(r * r for r in rpm)

        remaining_pct = raw.battery_remaining_pct if raw.battery_remaining_pct is not None else 50.0
        current_a = raw.battery_current_a if raw.battery_current_a is not None else 0.0
        v_oc = self._open_circuit_voltage(remaining_pct)
        # By the time telemetry reaches M3 it should already be gap-filled
        # by M2; this fallback only matters if M3 is driven standalone.
        voltage_v = raw.battery_voltage_v if raw.battery_voltage_v is not None else v_oc
        vertical_accel = raw.accel_xyz[2]

        z = np.array([[voltage_v], [vertical_accel]])

        def h_jacobian(x: np.ndarray) -> np.ndarray:
            mass, _resistance, efficiency = x[0, 0], x[1, 0], x[2, 0]
            thrust = self.K_THRUST * efficiency * sum_rpm_sq
            return np.array(
                [
                    [0.0, -current_a, 0.0],
                    [-thrust / (mass**2), 0.0, self.K_THRUST * sum_rpm_sq / mass],
                ]
            )

        def hx(x: np.ndarray) -> np.ndarray:
            mass, resistance, efficiency = x[0, 0], x[1, 0], x[2, 0]
            thrust = self.K_THRUST * efficiency * sum_rpm_sq
            h1 = v_oc - current_a * resistance
            h2 = thrust / mass
            return np.array([[h1], [h2]])

        self.ekf.predict()
        self.ekf.update(z, h_jacobian, hx)

        innovation = self.ekf.y.flatten()
        measurement_sigma = np.sqrt(np.diag(self.ekf.R))
        deviation = float(np.sqrt(np.sum((innovation / measurement_sigma) ** 2)))

        mass_est, resistance_est, efficiency_est = self.ekf.x.flatten().tolist()
        mass_est = max(mass_est, 0.1)  # keep physically sensible / avoid divide-by-zero next cycle
        resistance_est = max(resistance_est, 0.0)

        return TwinParams(
            timestamp=raw.timestamp,
            effective_mass_kg=mass_est,
            motor_thrust_efficiency=self._per_motor_efficiency(rpm, efficiency_est),
            battery_internal_resistance_ohm=resistance_est,
            deviation=deviation,
        )

    def _open_circuit_voltage(self, remaining_pct: float) -> float:
        pct = max(0.0, min(100.0, remaining_pct))
        return self.V_EMPTY + (self.V_FULL - self.V_EMPTY) * (pct / 100.0)

    @staticmethod
    def _per_motor_efficiency(rpm: list[float], global_efficiency: float) -> list[float]:
        """We can only identify ONE global efficiency parameter from the
        EKF — there's no independent per-motor thrust sensor in the
        telemetry we have. As a proxy for per-motor differences, we
        distribute the global estimate across motors by each motor's RPM
        share relative to the fleet average this cycle: a motor spinning
        slower than its siblings for the same commanded PWM shows up as
        below-average efficiency. This is a heuristic, not an
        independently-identified per-motor estimate — true per-motor
        system identification would need per-motor current sensing."""
        if not rpm:
            return [global_efficiency]
        mean_rpm = sum(rpm) / len(rpm)
        if mean_rpm <= 0:
            return [global_efficiency] * len(rpm)
        return [global_efficiency * (r / mean_rpm) for r in rpm]
