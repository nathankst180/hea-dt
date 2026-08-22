// Mirrors contracts/schemas.py (backend) as serialized by
// api/serialization.py::cycle_result_to_dict(). One CycleSnapshot arrives
// per WebSocket message, one per pipeline cycle (M1 -> M12).
//
// Tuple fields (accel_xyz, position_lla, ...) come across as plain JSON
// arrays — typed here as fixed-length tuples for a little extra safety
// when indexing into them (e.g. position_lla[2] is altitude).

export type Vec3 = [number, number, number];

export interface RawState {
  timestamp: number;
  battery_voltage_v: number | null;
  battery_current_a: number | null;
  battery_remaining_pct: number | null;
  battery_temp_c: number | null;
  motor_pwm: number[];
  motor_rpm: number[];
  esc_current_a: number[];
  esc_temp_c: number[];
  accel_xyz: Vec3;
  gyro_xyz: Vec3;
  attitude_rpy: Vec3; // roll, pitch, yaw (radians)
  position_lla: Vec3; // lat (deg), lon (deg), alt (m)
  velocity_ned: Vec3;
  airspeed_ms: number | null;
  groundspeed_ms: number | null;
  wind_ned: Vec3;
  gps_fix_type: number;
  gps_satellites_visible: number;
  armed: boolean;
  flight_mode: string;
}

export interface StateVector {
  raw: RawState;
  validity: Record<string, boolean>;
  signal_sigma: Record<string, number>;
  sensor_fault_flags: string[];
}

export interface TwinParams {
  timestamp: number;
  effective_mass_kg: number;
  motor_thrust_efficiency: number[];
  battery_internal_resistance_ohm: number;
  deviation: number;
}

export interface HealthElectrical {
  timestamp: number;
  battery_soh_pct: number;
  battery_rul_s: number | null; // null <- float('inf') sanitized on the backend
  motor_health: number[];
  esc_health: number[];
  p_fail_horizon: number;
  confidence: number;
}

export interface HealthStructural {
  timestamp: number;
  imbalance_flag: boolean;
  prop_damage_score: number;
  structural_anomaly_score: number;
  per_rotor_scores: number[];
  confidence: number;
}

export interface EnergyPlan {
  timestamp: number;
  v_opt_ms: number;
  alt_opt_m: number;
  predicted_energy_wh: number;
  energy_margin_wh: number;
  optimized_waypoints: Vec3[];
}

export interface Confidence {
  timestamp: number;
  per_prediction: Record<string, number>;
  decision_confidence: number;
}

export interface Feasibility {
  timestamp: number;
  feasible: boolean;
  segment_margins: number[];
  risk_map: Record<string, number>;
  failure_before_completion: boolean;
}

export type DecisionKind = "NOMINAL" | "REOPTIMIZE" | "REDUCE_SCOPE" | "ABORT";

export interface Decision {
  timestamp: number;
  kind: DecisionKind;
  params: Record<string, unknown>;
  triggered_by: string;
}

export type CommandKind =
  | "SET_SPEED"
  | "REROUTE"
  | "SET_GUIDED_SETPOINT"
  | "REDUCE_SCOPE"
  | "RETURN_TO_LAUNCH"
  | "LAND"
  | "SET_PARAM";

export interface Command {
  timestamp: number;
  kind: CommandKind;
  payload: Record<string, unknown>;
  reversible: boolean;
  requires_human: boolean;
}

export interface Ack {
  timestamp: number;
  accepted: boolean;
  latency_ms: number;
  detail: string;
}

export interface LogRecord {
  timestamp: number;
  decision: Decision;
  command: Command | null;
  ack: Ack | null;
  rationale: string;
  prev_hash: string;
  record_hash: string;
}

export interface CycleSnapshot {
  cycle: number;
  raw_state: RawState;
  state_vector: StateVector;
  twin_params: TwinParams;
  health_electrical: HealthElectrical;
  health_structural: HealthStructural;
  energy_plan: EnergyPlan;
  confidence: Confidence;
  feasibility: Feasibility;
  decision: Decision;
  command: Command | null;
  ack: Ack | null;
  log_record: LogRecord;
}

// ---- REST API response shapes -------------------------------------------

export interface UploadResponse {
  session_id: string;
  filename: string;
  num_rows: number;
  duration_s: number;
  start_time: number;
  end_time: number;
}

export type SessionStatus = "IDLE" | "RUNNING" | "STOPPED" | "FINISHED" | "ERROR";

export interface ReplayStatusResponse {
  session_id: string;
  status: SessionStatus;
  cycles_produced: number;
  error_detail: string | null;
}

export interface LogsResponse {
  session_id: string;
  records: LogRecord[];
}

export const SPEED_OPTIONS = [1, 2, 5, 10] as const;
export type SpeedMultiplier = (typeof SPEED_OPTIONS)[number];
