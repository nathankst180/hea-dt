"""
modules/m1_telemetry/gateway.py

M1 — Telemetry Acquisition & MAVLink Gateway (real implementation).

Connects to a running PX4 or ArduPilot SITL instance over MAVLink using
pymavlink, subscribes to the telemetry messages we need, and assembles
them into RawState objects. Also implements the WRITE side: a test
command (MAV_CMD_DO_CHANGE_SPEED) sent through the same connection and
confirmed via COMMAND_ACK, proving the channel is bidirectional from the
start — M11 (Phase 4) builds the full sanctioned-command pipeline on top
of this same pattern.

MAVLink notes for readers new to it (kept inline, not just here):
  - Messages arrive asynchronously and at different rates; there is no
    single "telemetry frame". We keep the LATEST message of each type
    we've seen and assemble a RawState from whatever's freshest each time
    get_raw_state() is called. A message type we haven't received yet
    (e.g. ESC_STATUS, which many SITL setups don't send) just leaves the
    corresponding RawState fields at their defaults (None / empty list) —
    that's "no data yet", not "zero".
  - Units are message-specific and are NOT SI by default — e.g. angles in
    degE7, current in centiamps, accel in milli-g. Each parsing function
    below documents the conversion it applies, citing the field name it
    came from, so it can be checked against the MAVLink common.xml spec
    if a real SITL's numbers ever look off by a factor of 10/100/1000.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

from pymavlink import mavutil

from contracts.schemas import Ack, RawState
from modules.m1_telemetry.base import TelemetrySource

logger = logging.getLogger("hea_dt.m1_telemetry")

_MG_TO_MS2 = 9.80665 / 1000.0  # milli-g -> m/s^2
_MRAD_S_TO_RAD_S = 1.0 / 1000.0  # milli-rad/s -> rad/s
_DEG_E7_TO_DEG = 1e-7  # degE7 -> degrees
_MM_TO_M = 1.0 / 1000.0
_CM_S_TO_M_S = 1.0 / 100.0
_CA_TO_A = 1.0 / 100.0  # centiamps -> amps
_CDEGC_TO_C = 1.0 / 100.0  # centi-degC -> degC


class MAVLinkGateway(TelemetrySource):
    """Real bidirectional MAVLink connection to a PX4/ArduPilot SITL instance."""

    def __init__(
        self,
        connection_string: str = "udp:127.0.0.1:14540",
        ring_buffer_size: int = 200,
        heartbeat_timeout_s: float = 5.0,
        command_ack_timeout_s: float = 3.0,
        n_motors: int = 4,
    ):
        super().__init__(ring_buffer_size)
        self.connection_string = connection_string
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.command_ack_timeout_s = command_ack_timeout_s
        self.n_motors = n_motors
        self._conn = None
        self._latest: dict[str, Any] = {}

    def connect(self) -> None:
        """Open the MAVLink connection and wait for the first heartbeat.

        Raises ConnectionError if no heartbeat arrives in time — this is
        how we tell "SITL isn't running/reachable" apart from silently
        hanging forever.
        """
        logger.info("M1 connecting to %s ...", self.connection_string)
        self._conn = mavutil.mavlink_connection(self.connection_string)
        heartbeat = self._conn.wait_heartbeat(timeout=self.heartbeat_timeout_s)
        if heartbeat is None:
            raise ConnectionError(
                f"No MAVLink heartbeat received from {self.connection_string} "
                f"within {self.heartbeat_timeout_s}s — is PX4/ArduPilot SITL "
                f"running and reachable? (start it, or run the orchestrator "
                f"with --mock)"
            )
        logger.info(
            "M1 connected: system=%d component=%d",
            self._conn.target_system,
            self._conn.target_component,
        )

    def _pump_messages(self, drain_time_s: float = 0.02) -> None:
        """Drain whatever MAVLink messages have arrived, keeping only the
        latest of each type. Non-blocking beyond drain_time_s so a call to
        get_raw_state() returns promptly even if the link is quiet."""
        deadline = time.time() + drain_time_s
        while time.time() < deadline:
            msg = self._conn.recv_match(blocking=False)
            if msg is None:
                break
            self._latest[msg.get_type()] = msg

    def get_raw_state(self) -> RawState:
        if self._conn is None:
            self.connect()
        self._pump_messages()
        raw = self._assemble_raw_state()
        return self._record(raw)

    def _assemble_raw_state(self) -> RawState:
        battery = self._latest.get("BATTERY_STATUS")
        imu = self._latest.get("SCALED_IMU") or self._latest.get("RAW_IMU") or self._latest.get("HIGHRES_IMU")
        attitude = self._latest.get("ATTITUDE")
        gps = self._latest.get("GPS_RAW_INT")
        heartbeat = self._latest.get("HEARTBEAT")
        servo = self._latest.get("SERVO_OUTPUT_RAW")
        esc = self._latest.get("ESC_STATUS") or self._latest.get("ESC_INFO")
        vfr_hud = self._latest.get("VFR_HUD")
        global_pos = self._latest.get("GLOBAL_POSITION_INT")
        wind = self._latest.get("WIND")

        battery_voltage_v, battery_current_a, battery_remaining_pct, battery_temp_c = self._parse_battery(battery)
        accel_xyz, gyro_xyz = self._parse_imu(imu)
        attitude_rpy = self._parse_attitude(attitude)
        position_lla, velocity_ned = self._parse_position(gps, global_pos)
        gps_fix_type, gps_satellites_visible = self._parse_gps_quality(gps)
        motor_pwm = self._parse_servo(servo)
        motor_rpm, esc_current_a, esc_temp_c = self._parse_esc(esc)
        airspeed_ms, groundspeed_ms = self._parse_vfr_hud(vfr_hud)
        wind_ned = self._parse_wind(wind)
        armed, flight_mode = self._parse_status(heartbeat)

        return RawState(
            timestamp=time.time(),
            battery_voltage_v=battery_voltage_v,
            battery_current_a=battery_current_a,
            battery_remaining_pct=battery_remaining_pct,
            battery_temp_c=battery_temp_c,
            motor_pwm=motor_pwm,
            motor_rpm=motor_rpm,
            esc_current_a=esc_current_a,
            esc_temp_c=esc_temp_c,
            accel_xyz=accel_xyz,
            gyro_xyz=gyro_xyz,
            attitude_rpy=attitude_rpy,
            position_lla=position_lla,
            velocity_ned=velocity_ned,
            airspeed_ms=airspeed_ms,
            groundspeed_ms=groundspeed_ms,
            wind_ned=wind_ned,
            gps_fix_type=gps_fix_type,
            gps_satellites_visible=gps_satellites_visible,
            armed=armed,
            flight_mode=flight_mode,
        )

    # ---- per-message parsers -------------------------------------------------
    # Each of these takes the raw pymavlink message (or None if we haven't
    # seen that type yet) and returns plain Python values in SI units.

    @staticmethod
    def _parse_battery(msg) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        if msg is None:
            return None, None, None, None
        # BATTERY_STATUS.voltages[] is per-cell millivolts; 0xFFFF marks an
        # unused cell slot. Summing the valid ones gives total pack voltage.
        voltages = [v for v in getattr(msg, "voltages", []) if v not in (0xFFFF, 65535)]
        voltage_v = sum(voltages) / 1000.0 if voltages else None
        current_ca = getattr(msg, "current_battery", -1)
        current_a = current_ca * _CA_TO_A if current_ca != -1 else None
        remaining = getattr(msg, "battery_remaining", -1)
        remaining_pct = float(remaining) if remaining != -1 else None
        temp_cdeg = getattr(msg, "temperature", 32767)
        temp_c = temp_cdeg * _CDEGC_TO_C if temp_cdeg != 32767 else None
        return voltage_v, current_a, remaining_pct, temp_c

    @staticmethod
    def _parse_imu(msg) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if msg is None:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        # RAW_IMU/SCALED_IMU accel fields are milli-g, gyro fields are
        # milli-rad/s (PX4/ArduPilot SITL convention for both message types).
        accel = (msg.xacc * _MG_TO_MS2, msg.yacc * _MG_TO_MS2, msg.zacc * _MG_TO_MS2)
        gyro = (msg.xgyro * _MRAD_S_TO_RAD_S, msg.ygyro * _MRAD_S_TO_RAD_S, msg.zgyro * _MRAD_S_TO_RAD_S)
        return accel, gyro

    @staticmethod
    def _parse_attitude(msg) -> tuple[float, float, float]:
        if msg is None:
            return (0.0, 0.0, 0.0)
        return (msg.roll, msg.pitch, msg.yaw)  # already radians

    @staticmethod
    def _parse_position(gps_raw, global_pos) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        # Prefer GLOBAL_POSITION_INT: it's the autopilot's filtered
        # estimate (position + velocity together), vs. GPS_RAW_INT which
        # is the raw GPS receiver output with no velocity vector.
        if global_pos is not None:
            position = (
                global_pos.lat * _DEG_E7_TO_DEG,
                global_pos.lon * _DEG_E7_TO_DEG,
                global_pos.alt * _MM_TO_M,
            )
            velocity = (
                global_pos.vx * _CM_S_TO_M_S,
                global_pos.vy * _CM_S_TO_M_S,
                global_pos.vz * _CM_S_TO_M_S,
            )
            return position, velocity
        if gps_raw is not None:
            position = (gps_raw.lat * _DEG_E7_TO_DEG, gps_raw.lon * _DEG_E7_TO_DEG, gps_raw.alt * _MM_TO_M)
            return position, (0.0, 0.0, 0.0)
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

    @staticmethod
    def _parse_gps_quality(msg) -> tuple[int, int]:
        if msg is None:
            return 0, 0
        satellites = msg.satellites_visible
        return msg.fix_type, (satellites if satellites != 255 else 0)

    def _parse_servo(self, msg) -> list[float]:
        if msg is None:
            return []
        pwm = []
        for i in range(1, self.n_motors + 1):
            value = getattr(msg, f"servo{i}_raw", None)
            if value is None:
                break
            pwm.append(float(value))
        return pwm

    @staticmethod
    def _parse_esc(msg) -> tuple[list[float], list[float], list[float]]:
        # ESC_STATUS/ESC_INFO are newer messages many SITL setups don't
        # stream; if unavailable, motor_rpm/esc_current_a/esc_temp_c stay
        # empty and downstream modules treat that as "data unavailable",
        # not "zero" (see contracts/schemas.py docstrings).
        if msg is None:
            return [], [], []
        rpm = list(getattr(msg, "rpm", []))
        current_ca = getattr(msg, "current", [])
        current_a = [c * _CA_TO_A for c in current_ca] if current_ca else []
        # ESC_STATUS doesn't carry temperature in the common dialect; leave empty.
        temp_c: list[float] = []
        return [float(r) for r in rpm], current_a, temp_c

    @staticmethod
    def _parse_vfr_hud(msg) -> tuple[Optional[float], Optional[float]]:
        if msg is None:
            return None, None
        return msg.airspeed, msg.groundspeed

    @staticmethod
    def _parse_wind(msg) -> tuple[float, float, float]:
        if msg is None:
            return (0.0, 0.0, 0.0)
        # WIND is polar (direction in degrees the wind is coming FROM,
        # speed in m/s); convert to a North-East wind vector.
        direction_rad = math.radians(msg.direction)
        north = msg.speed * math.cos(direction_rad)
        east = msg.speed * math.sin(direction_rad)
        return (north, east, getattr(msg, "speed_z", 0.0))

    @staticmethod
    def _parse_status(heartbeat) -> tuple[bool, str]:
        if heartbeat is None:
            return False, ""
        armed = bool(heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        try:
            mode = mavutil.mode_string_v10(heartbeat)
        except Exception:
            mode = str(heartbeat.custom_mode)
        return armed, mode

    # ---- write side ------------------------------------------------------

    def send_test_command(self, speed_ms: float) -> Ack:
        """Send MAV_CMD_DO_CHANGE_SPEED and wait for COMMAND_ACK.

        This proves the gateway is genuinely bidirectional: it's the same
        connection object used for reading telemetry, just calling
        .mav.command_long_send() instead of .recv_match().
        """
        if self._conn is None:
            self.connect()

        t_send = time.time()
        self._conn.mav.command_long_send(
            self._conn.target_system,
            self._conn.target_component,
            mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0,  # confirmation
            1,  # param1: speed type, 1 = ground speed
            speed_ms,  # param2: target speed (m/s)
            -1,  # param3: throttle, -1 = no change
            0,
            0,
            0,
            0,  # param4-7: unused
        )
        ack_msg = self._conn.recv_match(type="COMMAND_ACK", blocking=True, timeout=self.command_ack_timeout_s)
        latency_ms = (time.time() - t_send) * 1000.0

        if ack_msg is None:
            logger.warning("MAV_CMD_DO_CHANGE_SPEED: no COMMAND_ACK within %.1fs", self.command_ack_timeout_s)
            return Ack(
                timestamp=time.time(),
                accepted=False,
                latency_ms=latency_ms,
                detail="no COMMAND_ACK received (timeout)",
            )

        accepted = ack_msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED
        logger.info(
            "MAV_CMD_DO_CHANGE_SPEED ack: result=%d accepted=%s latency=%.1fms",
            ack_msg.result,
            accepted,
            latency_ms,
        )
        return Ack(
            timestamp=time.time(),
            accepted=accepted,
            latency_ms=latency_ms,
            detail=f"MAV_RESULT={ack_msg.result}",
        )
