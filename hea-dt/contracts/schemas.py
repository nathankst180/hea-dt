"""
contracts/schemas.py

Shared data contracts for HEA-DT.

Every module in the pipeline (M1 through M12) reads and writes ONLY these
typed objects. Defining them up front means each module can be built,
tested, and swapped out independently, as long as it honors the shape of
its inputs/outputs here.

Pipeline order (each arrow means "produces the input for"):

    M1  -> RawState
    M2  -> StateVector
    M3  -> TwinParams
    M4  -> HealthElectrical   \\
    M5  -> HealthStructural     >  M4, M5, M6 all run off M3's output,
    M6  -> EnergyPlan          /   independently of each other
    M7  -> Confidence          (fuses M4/M5/M6's outputs + M3's deviation)
    M8  -> Feasibility         (fuses M4/M5/M6/M7 into one verdict)
    M9  -> Decision            (NOMINAL / REOPTIMIZE / REDUCE_SCOPE / ABORT)
    M10 -> Command              (Decision, checked against the safety envelope)
    M11 -> Ack                  (the autopilot's response to that Command)
    M12 -> LogRecord            (bundles Decision + Command + Ack for the audit trail)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# M1 -> M2 : RawState
# ---------------------------------------------------------------------------


@dataclass
class RawState:
    """
    One timestamped snapshot of everything the drone's flight controller
    told us, straight off MAVLink, with no cleaning or interpretation yet.

    Produced by: M1 (Telemetry Acquisition & MAVLink Gateway)
    Consumed by: M2 (Data Validation & Sensor-Fault Isolation)

    Fields are grouped by subsystem and named after the MAVLink message
    they come from. Per-motor/per-ESC fields are lists where index 0 is
    motor 1, index 1 is motor 2, and so on — keep these lists the same
    length and index-aligned with each other.
    """

    timestamp: float
    """Unix epoch seconds when this snapshot was assembled."""

    # --- battery (MAVLink BATTERY_STATUS) ---
    battery_voltage_v: Optional[float] = None
    """Pack voltage in volts. A voltage that sags more than usual under the
    same current draw is an early sign of an aging/damaged battery."""

    battery_current_a: Optional[float] = None
    """Instantaneous current draw in amps. High current means high power
    demand right now (e.g. climbing, fighting wind, carrying payload)."""

    battery_remaining_pct: Optional[float] = None
    """Flight controller's own estimate of charge remaining, 0-100. This is
    charge level, NOT health — a healthy battery and a worn-out battery can
    both report 100% right after a full charge."""

    battery_temp_c: Optional[float] = None
    """Battery pack temperature in Celsius. Running hot accelerates
    degradation and can itself be a symptom of high internal resistance."""

    # --- motors / ESCs (SERVO_OUTPUT_RAW, ESC_STATUS / ESC_INFO) ---
    motor_pwm: list[float] = field(default_factory=list)
    """Commanded PWM pulse width in microseconds sent to each motor, one
    entry per motor (typically ~1000-2000us range)."""

    motor_rpm: list[float] = field(default_factory=list)
    """Measured RPM of each motor, one entry per motor, index-aligned with
    motor_pwm. Comparing PWM commanded vs. RPM achieved is how motor
    degradation shows up."""

    esc_current_a: list[float] = field(default_factory=list)
    """Current draw reported by each Electronic Speed Controller, in amps,
    one entry per motor."""

    esc_temp_c: list[float] = field(default_factory=list)
    """Temperature reported by each ESC, in Celsius, one entry per motor."""

    # --- IMU (RAW_IMU / SCALED_IMU / HIGHRES_IMU) ---
    accel_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Linear acceleration in the aircraft's own body frame (x=forward,
    y=right, z=down), in m/s^2. The high-frequency wiggle in this signal
    (vibration) is what M5 uses to detect structural/propeller problems —
    no extra sensor needed."""

    gyro_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Angular rate in the body frame (roll-rate, pitch-rate, yaw-rate),
    in rad/s."""

    # --- attitude & motion (ATTITUDE, VFR_HUD, GLOBAL_POSITION_INT) ---
    attitude_rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Roll, pitch, yaw of the aircraft, in radians."""

    position_lla: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Latitude (degrees), longitude (degrees), altitude above sea level
    (meters)."""

    velocity_ned: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Velocity vector in the North-East-Down frame, m/s."""

    airspeed_ms: Optional[float] = None
    """Speed relative to the surrounding air, m/s (from a pitot tube, if
    the airframe has one). Differs from groundspeed when there's wind."""

    groundspeed_ms: Optional[float] = None
    """Speed relative to the ground, m/s (from GPS)."""

    # --- environment ---
    wind_ned: tuple[float, float, float] = (0.0, 0.0, 0.0)
    """Autopilot's estimated wind vector, North-East-Down frame, m/s. Feeds
    M6's energy model — headwind costs more power than tailwind."""

    # --- GPS (GPS_RAW_INT) ---
    gps_fix_type: int = 0
    """MAVLink GPS fix quality: 0=no fix, 2=2D fix, 3=3D fix, 4-6=RTK
    variants (higher = better)."""

    gps_satellites_visible: int = 0
    """Number of GPS satellites currently used in the position solution."""

    # --- overall system status (SYS_STATUS, HEARTBEAT) ---
    armed: bool = False
    """True if the autopilot is armed (motors are allowed to spin)."""

    flight_mode: str = ""
    """Current flight mode string, e.g. 'AUTO', 'GUIDED', 'RTL', 'LAND'."""


# ---------------------------------------------------------------------------
# M2 -> M3 : StateVector
# ---------------------------------------------------------------------------


@dataclass
class StateVector:
    """
    A cleaned, quality-checked version of RawState. This is what every
    downstream module (M3 onward) should actually read — never RawState
    directly, since RawState hasn't been checked for sensor glitches yet.

    Produced by: M2 (Data Validation & Sensor-Fault Isolation)
    Consumed by: M3 (Digital-Twin State Estimation)
    """

    raw: RawState
    """The telemetry itself, in the same shape as RawState. Values may have
    been corrected or imputed by M2 (e.g. a dropped GPS sample filled in
    from the last good fix)."""

    validity: dict[str, bool] = field(default_factory=dict)
    """Per-field pass/fail flag from this cycle's checks, e.g.
    {'battery_voltage_v': True, 'gps_fix_type': False}. False means that
    field failed a plausibility or cross-sensor consistency check and
    should be treated with suspicion downstream."""

    signal_sigma: dict[str, float] = field(default_factory=dict)
    """Per-field noise estimate (standard deviation) for this cycle, used
    by M3's filter to weight how much to trust each measurement, and by
    M7 to propagate uncertainty."""

    sensor_fault_flags: list[str] = field(default_factory=list)
    """Names of fields currently believed to be reporting bad data because
    of a FAULTY SENSOR — as opposed to a real underlying component
    problem. E.g. ['gps_fix_type'] means "the GPS itself looks glitchy
    right now", which should NOT be read downstream as "the aircraft is
    actually lost". This sensor-vs-real-fault split is what stops a single
    flaky sensor from triggering a false abort."""


# ---------------------------------------------------------------------------
# M3 -> M4, M5, M6, M8 : TwinParams
# ---------------------------------------------------------------------------


@dataclass
class TwinParams:
    """
    The digital twin's current best estimate of the aircraft's own physical
    parameters — the numbers that quietly drift over a flight (payload
    shift, battery aging) or over the aircraft's life, and that a generic
    datasheet model would get wrong.

    Produced by: M3 (Digital-Twin State Estimation & Self-Calibration)
    Consumed by: M4, M5, M6 (need the *current* aircraft, not the nominal
    one), M8 (fusion), M10 (deviation gates autonomy)
    """

    timestamp: float
    """Unix epoch seconds this estimate was computed."""

    effective_mass_kg: float = 0.0
    """Estimated total mass including any payload, in kilograms."""

    motor_thrust_efficiency: list[float] = field(default_factory=list)
    """Estimated thrust-per-unit-current efficiency for each motor, one
    entry per motor, normalized so 1.0 = brand-new/nominal performance. A
    value of 0.8 means that motor is producing about 80% of the thrust a
    healthy motor would for the same current draw — an early sign of
    winding wear or a partially failed ESC."""

    battery_internal_resistance_ohm: float = 0.0
    """Estimated internal resistance of the battery pack, in ohms. This
    rises as a battery ages and directly causes voltage to sag more under
    the same current load."""

    deviation: float = 0.0
    """δ(t): the distance between what the twin's physics model *predicted*
    the state would be and what was actually *measured* this cycle (e.g. a
    residual norm from the estimator). Large deviation means the twin's
    internal model of the aircraft is currently a poor match for reality,
    and every downstream module should become more conservative — this is
    a first-class quantity that later gates autonomous decisions (M10),
    not just an internal filter diagnostic."""


# ---------------------------------------------------------------------------
# M4 -> M7, M8 : HealthElectrical
# ---------------------------------------------------------------------------


@dataclass
class HealthElectrical:
    """
    Predicted electrical/powertrain health: how much life is left in the
    battery and motors, and how likely a failure is in the near future.

    Produced by: M4 (Electrical & Powertrain RUL Prediction)
    Consumed by: M7 (Uncertainty Quantification), M8 (Fusion)
    """

    timestamp: float
    """Unix epoch seconds this prediction was made."""

    battery_soh_pct: float = 100.0
    """Battery State of Health, 0-100. 100 means the pack still holds as
    much usable capacity as when new; this falls with age/wear cycles and
    is distinct from battery_remaining_pct in RawState (which is today's
    charge level, not the pack's long-term health)."""

    battery_rul_s: float = float("inf")
    """Battery Remaining Useful Life: predicted seconds of safe flight time
    left, given the current draw pattern and degradation trend."""

    motor_health: list[float] = field(default_factory=list)
    """Predicted health score per motor, 0-1 (1.0 = fully healthy), one
    entry per motor."""

    esc_health: list[float] = field(default_factory=list)
    """Predicted health score per ESC, 0-1 (1.0 = fully healthy), one
    entry per ESC."""

    p_fail_horizon: float = 0.0
    """Probability (0-1) of a powertrain failure (battery or motor/ESC)
    within the prediction horizon defined in config (e.g. "within the next
    5 minutes")."""

    confidence: float = 1.0
    """This module's own self-reported confidence in the numbers above,
    0-1. M7 combines this with other signals into a single fused
    decision_confidence."""


# ---------------------------------------------------------------------------
# M5 -> M7, M8 : HealthStructural
# ---------------------------------------------------------------------------


@dataclass
class HealthStructural:
    """
    Predicted mechanical/structural health, inferred purely from vibration
    (IMU) data — no extra sensors required. Propeller imbalance, loose
    mounts, and frame cracks all change the vibration signature before
    they show up any other way.

    Produced by: M5 (Structural Health Monitoring, vibration-based)
    Consumed by: M7 (Uncertainty Quantification), M8 (Fusion)
    """

    timestamp: float
    """Unix epoch seconds this prediction was made."""

    imbalance_flag: bool = False
    """True if a rotor imbalance (e.g. a nicked or dirty propeller) is
    currently detected in the vibration spectrum."""

    prop_damage_score: float = 0.0
    """Severity score for propeller damage, 0-1 (0 = none, 1 = severe)."""

    structural_anomaly_score: float = 0.0
    """General "this airframe's vibration signature looks abnormal" score,
    0-1 — e.g. a frame crack changing how the aircraft's structure
    resonates, distinct from a specific propeller/motor issue."""

    per_rotor_scores: list[float] = field(default_factory=list)
    """Anomaly score per rotor position, 0-1, one entry per rotor, so a
    problem can be localized to a specific arm/motor rather than just
    flagged aircraft-wide."""

    confidence: float = 1.0
    """This module's own self-reported confidence in the numbers above,
    0-1. Refined further by M7."""


# ---------------------------------------------------------------------------
# M6 -> M8, M9 : EnergyPlan
# ---------------------------------------------------------------------------


@dataclass
class EnergyPlan:
    """
    The power-optimal way to fly the rest of the mission given the
    aircraft's CURRENT (possibly degraded) condition — not generic
    datasheet performance.

    Produced by: M6 (Energy Modeling & Optimization)
    Consumed by: M8 (Fusion), M9 (Decision — applied when the decision is
    NOMINAL or REOPTIMIZE)
    """

    timestamp: float
    """Unix epoch seconds this plan was computed."""

    v_opt_ms: float = 0.0
    """Recommended cruise airspeed, in m/s, that minimizes power draw for
    the current mass/wind/battery condition."""

    alt_opt_m: float = 0.0
    """Recommended cruise altitude, in meters, for the same reason (air
    density and wind both vary with altitude)."""

    predicted_energy_wh: float = 0.0
    """Predicted total energy, in watt-hours, needed to complete the
    remaining mission if flown at the recommended speed/altitude."""

    energy_margin_wh: float = 0.0
    """predicted_available_energy_wh minus predicted_energy_wh. Positive
    means there's spare energy beyond what's needed; negative means the
    mission as currently planned would eat into (or exceed) the safety
    reserve defined in config."""

    optimized_waypoints: list[tuple[float, float, float]] = field(
        default_factory=list
    )
    """Optional re-planned route as a list of (lat, lon, alt_m) points,
    populated when there's a lower-energy path than the current mission —
    used by M9 when it decides to REOPTIMIZE."""


# ---------------------------------------------------------------------------
# M7 -> M8, M9, M10 : Confidence
# ---------------------------------------------------------------------------


@dataclass
class Confidence:
    """
    Calibrated uncertainty attached to every prediction, so the decision
    layer can be appropriately cautious when the twin isn't sure of
    itself, instead of treating every prediction as ground truth.

    Produced by: M7 (Uncertainty Quantification)
    Consumed by: M8 (Fusion), M9 (Decision), M10 (Safety Arbitration — this
    is the number confidence-gating is based on)
    """

    timestamp: float
    """Unix epoch seconds this confidence estimate was computed."""

    per_prediction: dict[str, float] = field(default_factory=dict)
    """Confidence (0-1) for each individual upstream prediction this
    cycle, keyed by name, e.g.
    {'battery_rul': 0.86, 'structural_anomaly': 0.72, 'energy_margin': 0.9}."""

    decision_confidence: float = 1.0
    """A single fused confidence score (0-1), combining the per-prediction
    confidences above with twin deviation (M3's δ(t)) and data validity
    (M2). This is the one number M10 actually gates autonomy on — low
    decision_confidence escalates a decision to a human instead of letting
    it execute automatically."""


# ---------------------------------------------------------------------------
# M8 -> M9 : Feasibility
# ---------------------------------------------------------------------------


@dataclass
class Feasibility:
    """
    The result of forward-simulating the rest of the mission under the
    current health and energy trend: will the drone actually make it, and
    where might it fail if not?

    Produced by: M8 (Health-Energy Fusion & Feasibility Forward-Simulation)
    Consumed by: M9 (Adaptive Mission Decision Engine)
    """

    timestamp: float
    """Unix epoch seconds this feasibility check was computed."""

    feasible: bool = True
    """True if the forward simulation predicts the mission completes
    safely, within the energy budget, and before any predicted failure."""

    segment_margins: list[float] = field(default_factory=list)
    """Predicted safety margin for each remaining mission segment (e.g. a
    combined energy/health margin in module-defined units), one entry per
    segment, in mission order. A negative value means that segment is
    predicted to fail."""

    risk_map: dict[str, float] = field(default_factory=dict)
    """Named risk scores (0-1) for completing the remaining mission, e.g.
    {'battery_depletion': 0.4, 'motor_failure': 0.1, 'structural_failure': 0.05}."""

    failure_before_completion: bool = False
    """True if the forward simulation predicts some failure will occur
    before the mission's final waypoint is reached."""


# ---------------------------------------------------------------------------
# M9 -> M10 : Decision
# ---------------------------------------------------------------------------


class DecisionKind(str, Enum):
    """The four graded actions M9 can choose, in increasing order of
    severity. A str Enum so it serializes cleanly to JSON/SQLite for M12's
    log without extra conversion code."""

    NOMINAL = "NOMINAL"
    """Mission is feasible as planned; apply M6's optimal speed/altitude
    and continue."""

    REOPTIMIZE = "REOPTIMIZE"
    """Feasible, but a lower-energy route/speed exists; push M6's updated
    optimized waypoints."""

    REDUCE_SCOPE = "REDUCE_SCOPE"
    """Not enough energy or health margin for the full mission; drop the
    lowest-value remaining waypoints and re-upload a shortened mission."""

    ABORT = "ABORT"
    """Predicted failure risk crossed a hard threshold; return-to-launch or
    guided descent, now."""


@dataclass
class Decision:
    """
    The mission-level action chosen by the adaptive decision policy — an
    explicit, defensible finite-state machine rather than a black box.

    Produced by: M9 (Adaptive Mission Decision Engine)
    Consumed by: M10 (Safety Arbitration & Bounded Autonomy)
    """

    timestamp: float
    """Unix epoch seconds this decision was made."""

    kind: DecisionKind = DecisionKind.NOMINAL
    """Which of the four graded actions was chosen."""

    params: dict = field(default_factory=dict)
    """Action-specific parameters, e.g. {'v_opt_ms': 12.0, 'alt_opt_m': 80.0}
    for NOMINAL/REOPTIMIZE, or {'dropped_waypoint_ids': [...]} for
    REDUCE_SCOPE. Left as a plain dict since its shape depends on kind."""

    triggered_by: str = ""
    """Plain-language reason this decision was made, e.g. 'predicted
    battery RUL below remaining mission energy need at 0.81 confidence'.
    Carried straight through into M12's audit log."""


# ---------------------------------------------------------------------------
# M10 -> M11 : Command   /   M11 -> M12 : Ack
# ---------------------------------------------------------------------------


class CommandKind(str, Enum):
    """The MAVLink-level actions M11 knows how to translate into real
    messages sent to the autopilot."""

    SET_SPEED = "SET_SPEED"
    """MAV_CMD_DO_CHANGE_SPEED."""

    REROUTE = "REROUTE"
    """Mission re-upload: MISSION_CLEAR_ALL followed by MISSION_ITEM_INT
    for each new waypoint."""

    SET_GUIDED_SETPOINT = "SET_GUIDED_SETPOINT"
    """SET_POSITION_TARGET_GLOBAL_INT — a single guided-mode setpoint
    rather than a full mission re-upload."""

    REDUCE_SCOPE = "REDUCE_SCOPE"
    """Mission re-upload containing only the retained (higher-value)
    waypoints."""

    RETURN_TO_LAUNCH = "RETURN_TO_LAUNCH"
    """MAV_CMD_NAV_RETURN_TO_LAUNCH."""

    LAND = "LAND"
    """MAV_CMD_NAV_LAND."""

    SET_PARAM = "SET_PARAM"
    """PARAM_SET — used sparingly, for direct parameter adaptation."""


@dataclass
class Command:
    """
    A sanctioned, ready-to-send MAVLink action — what's left after M10 has
    checked a Decision against the safety envelope (geofence, minimum
    altitude, battery reserve, reversibility, confidence) and either
    approved it or flagged it for a human.

    Produced by: M10 (Safety Arbitration & Bounded Autonomy)
    Consumed by: M11 (Closed-Loop Command & Actuation)
    """

    timestamp: float
    """Unix epoch seconds this command was sanctioned."""

    kind: CommandKind = CommandKind.SET_SPEED
    """Which MAVLink action to send."""

    payload: dict = field(default_factory=dict)
    """Kind-specific parameters, e.g. {'speed_ms': 10.0} for SET_SPEED, or
    {'waypoints': [(lat, lon, alt), ...]} for REROUTE/REDUCE_SCOPE."""

    reversible: bool = True
    """True if this action can be easily undone (e.g. a speed change, a
    minor reroute). False for things like an abort/land or a major scope
    cut. Drives M10's human-in-the-loop gating: reversible actions may run
    autonomously, irreversible ones may require confirmation."""

    requires_human: bool = False
    """True if M10 decided a human must confirm this command before M11
    is allowed to send it — set when the action is irreversible, or when
    decision_confidence (M7) is below the configured threshold."""


@dataclass
class Ack:
    """
    The autopilot's acknowledgement that a Command was received and
    accepted (or rejected), and how long that took.

    Produced by: M11 (Closed-Loop Command & Actuation)
    Consumed by: M12 (Explainability & Decision Log)
    """

    timestamp: float
    """Unix epoch seconds the acknowledgement was received."""

    accepted: bool = False
    """True if the autopilot accepted and is now executing the command."""

    latency_ms: float = 0.0
    """Time between sending the command and receiving this acknowledgement,
    in milliseconds. This is the write-back latency metric evaluated for
    RQ4 — how fast the loop actually closes."""

    detail: str = ""
    """Free-text detail from the autopilot, e.g. a MAVLink COMMAND_ACK
    result code or a NACK reason."""


# ---------------------------------------------------------------------------
# M12 : LogRecord
# ---------------------------------------------------------------------------


@dataclass
class LogRecord:
    """
    One immutable, auditable entry tying together a Decision, the Command
    it produced, and the autopilot's Ack — plus a hash link to the
    previous entry so the log can't be silently edited after the fact.

    Produced by: M12 (Explainability & Immutable Decision Log)
    Consumed by: nothing downstream in the live control loop — this is the
    audit trail used for post-flight review, the paper's evaluation
    figures, and as training data for M14's fleet learning.
    """

    timestamp: float
    """Unix epoch seconds this log entry was created."""

    decision: Decision
    """The mission decision that was made this cycle."""

    command: Optional[Command] = None
    """The sanctioned command that resulted, if any. None if M10 blocked
    the action outright, or if it's awaiting a human who hasn't responded
    yet."""

    ack: Optional[Ack] = None
    """The autopilot's response, if a command was actually sent."""

    rationale: str = ""
    """Human-readable explanation of why, expanding on decision.triggered_by
    with the specific predictions, confidence, and twin deviation values
    behind it — the "why did the twin do that" answer for an auditor."""

    prev_hash: str = ""
    """SHA-256 hex digest of the previous LogRecord in the chain; empty
    string for the very first record. Makes the log tamper-evident: change
    an old entry and every hash after it stops matching."""

    record_hash: str = ""
    """SHA-256 hex digest of this record's own content (including
    prev_hash), computed and filled in by M12 when the record is written."""
