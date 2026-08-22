"""
modules/m1_telemetry/mock_source.py

M1 — Mock telemetry source. Used for every phase of development that
doesn't require a live Gazebo/SITL window open, and for the automated
test suite.

The battery model here deliberately uses the SAME open-circuit-voltage
approximation and internal-resistance idea that modules/m3_twin_state
assumes (see estimator.py's docstring) — so a --mock run demonstrates a
sensible, physically-plausible RawState -> StateVector -> TwinParams flow
rather than pure noise. Injected "faults" (MockFaultConfig) let you push
this generator away from that nominal model on purpose, to test that
downstream modules (M2, M3) actually notice.
"""

from __future__ import annotations

import logging
import math
import random
import time
from dataclasses import dataclass

from contracts.schemas import Ack, RawState
from modules.m1_telemetry.base import TelemetrySource

logger = logging.getLogger("hea_dt.m1_telemetry")


@dataclass
class MockFaultConfig:
    """Tunable fault injection for MockTelemetrySource. All faults default
    to "off" (0.0) so a plain MockTelemetrySource() is fully nominal."""

    battery_decline_v_per_s: float = 0.0
    """Extra voltage sag per second of wall-clock run time, ON TOP OF the
    normal discharge curve — simulates a battery that's degrading/aging
    faster than its internal-resistance model would predict, which is
    exactly the scenario M3's deviation metric should catch."""

    vibration_noise_std: float = 0.0
    """Extra Gaussian noise (std, in m/s^2) added to every accel_xyz axis
    each cycle — simulates a developing structural/propeller vibration
    problem for M5 to eventually detect."""

    glitch_prob: float = 0.0
    """Per-cycle probability (0-1) of a single-cycle SENSOR glitch (see
    glitch_field) — an isolated, physically-implausible reading that
    reverts next cycle. Used to test M2's sensor-vs-component-fault
    disambiguation."""

    glitch_field: str = "gps"
    """Which channel glitches when glitch_prob fires: "gps" (fix lost for
    one cycle), "battery_voltage" (implausible spike), or "imu" (a huge
    one-cycle accel spike)."""


def apply_sensor_glitch(
    field: str,
    battery_v: float,
    gps_fix_type: int,
    gps_satellites: int,
    accel_xyz: tuple[float, float, float],
    rng: random.Random,
) -> tuple[float, int, int, tuple[float, float, float]]:
    """Apply a single-cycle, isolated, physically-implausible glitch to
    one field — the exact shape M2's SensorValidator is designed to catch
    and flag as a SENSOR fault (see modules/m2_validation/validator.py):
    a discontinuous, out-of-family value with nothing else in the same
    cycle explaining it. A standalone function (not a method) so it can
    be reused verbatim by anything that wants this exact fault pattern —
    both MockTelemetrySource (live/interactive mock data) and
    data/generate_fault_data.py (the sensor_glitch training scenario) call
    this same code, so the dataset's glitches are the identical shape M2
    was built against, not a re-implementation that might drift."""
    if field == "battery_voltage":
        battery_v = rng.choice([0.0, 99.0])
    elif field == "gps":
        gps_fix_type, gps_satellites = 0, 0
    elif field == "imu":
        accel_xyz = (accel_xyz[0] + rng.choice([-1, 1]) * 40.0, accel_xyz[1], accel_xyz[2])
    return battery_v, gps_fix_type, gps_satellites, accel_xyz


class MockTelemetrySource(TelemetrySource):
    """Generates plausible fake RawState telemetry with no simulator
    running, optionally with injected faults."""

    _V_EMPTY = 13.2  # pack voltage at 0% remaining (linear discharge-curve approx, matches m3_twin_state)
    _V_FULL = 16.8  # pack voltage at 100% remaining
    _NOMINAL_R_OHM = 0.05
    _NOMINAL_HOVER_RPM = 5000.0
    _DISCHARGE_PCT_PER_S = 0.02  # ~80 minutes from 95% to near-empty at nominal current

    def __init__(
        self,
        seed: int = 42,
        ring_buffer_size: int = 200,
        faults: MockFaultConfig | None = None,
        n_motors: int = 4,
    ):
        super().__init__(ring_buffer_size)
        self._rng = random.Random(seed)
        self.faults = faults or MockFaultConfig()
        self.n_motors = n_motors
        self._t0 = time.time()
        self._last_t = self._t0
        self._remaining_pct = 95.0

    def get_raw_state(self) -> RawState:
        now = time.time()
        dt = max(now - self._last_t, 1e-3)
        self._last_t = now
        elapsed = now - self._t0

        current_a = self._rng.uniform(6.0, 14.0)
        self._remaining_pct = max(0.0, self._remaining_pct - self._DISCHARGE_PCT_PER_S * dt)
        v_oc = self._V_EMPTY + (self._V_FULL - self._V_EMPTY) * (self._remaining_pct / 100.0)
        battery_v = v_oc - current_a * self._NOMINAL_R_OHM
        battery_v -= self.faults.battery_decline_v_per_s * elapsed
        battery_v += self._rng.uniform(-0.05, 0.05)

        vib = self.faults.vibration_noise_std
        rpm = [self._NOMINAL_HOVER_RPM + self._rng.uniform(-150, 150) for _ in range(self.n_motors)]
        pwm = [1500.0 + self._rng.uniform(-30, 30) for _ in range(self.n_motors)]
        accel_xyz = (
            self._rng.uniform(-0.3, 0.3) + self._rng.gauss(0.0, vib),
            self._rng.uniform(-0.3, 0.3) + self._rng.gauss(0.0, vib),
            9.81 + self._rng.uniform(-0.1, 0.1) + self._rng.gauss(0.0, vib),
        )
        gyro_xyz = tuple(self._rng.gauss(0.0, 0.01 + vib * 0.1) for _ in range(3))

        gps_fix_type = 3
        gps_satellites = self._rng.randint(9, 14)

        # Ground speed and heading drive BOTH velocity_ned and
        # groundspeed_ms from the same numbers, so M2's GPS-vs-IMU
        # cross-consistency check compares two views of the same motion
        # instead of two independently-random fields (which would make it
        # false-flag a "GPS glitch" every cycle even with no faults injected).
        ground_speed = self._rng.uniform(9.0, 11.0)
        heading_rad = self._rng.uniform(-math.pi, math.pi)

        if self._rng.random() < self.faults.glitch_prob:
            battery_v, gps_fix_type, gps_satellites, accel_xyz = apply_sensor_glitch(
                self.faults.glitch_field, battery_v, gps_fix_type, gps_satellites, accel_xyz, self._rng
            )

        raw = RawState(
            timestamp=now,
            battery_voltage_v=battery_v,
            battery_current_a=current_a,
            battery_remaining_pct=self._remaining_pct,
            battery_temp_c=25.0 + self._rng.uniform(-2.0, 5.0),
            motor_pwm=pwm,
            motor_rpm=rpm,
            esc_current_a=[current_a / self.n_motors] * self.n_motors,
            esc_temp_c=[30.0 + self._rng.uniform(-3.0, 10.0) for _ in range(self.n_motors)],
            accel_xyz=accel_xyz,
            gyro_xyz=gyro_xyz,
            attitude_rpy=(
                self._rng.uniform(-0.05, 0.05),
                self._rng.uniform(-0.05, 0.05),
                self._rng.uniform(-math.pi, math.pi),
            ),
            position_lla=(47.3977, 8.5456, 100.0 + self._rng.uniform(-2, 2)),
            velocity_ned=(ground_speed * math.cos(heading_rad), ground_speed * math.sin(heading_rad), 0.0),
            airspeed_ms=ground_speed + self._rng.uniform(-1.0, 1.0),
            groundspeed_ms=ground_speed,
            wind_ned=(0.5, 0.2, 0.0),
            gps_fix_type=gps_fix_type,
            gps_satellites_visible=gps_satellites,
            armed=True,
            flight_mode="AUTO",
        )
        return self._record(raw)

    def send_test_command(self, speed_ms: float) -> Ack:
        logger.info(
            "MockTelemetrySource: pretending to send MAV_CMD_DO_CHANGE_SPEED speed_ms=%.1f",
            speed_ms,
        )
        return Ack(
            timestamp=time.time(),
            accepted=True,
            latency_ms=1.0,
            detail="mock ack — no real MAVLink connection (send_test_command stubbed by MockTelemetrySource)",
        )
