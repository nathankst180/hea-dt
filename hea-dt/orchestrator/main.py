"""
orchestrator/main.py

The main HEA-DT loop: calls M1 through M12 in sequence, once per pipeline
cycle, wiring each module's output into the next module's input via the
shared contracts in contracts/schemas.py.

Layer A (M1 Telemetry, M2 Validation, M3 Twin State) is now real —
Phase 1. Each of those three is a STATEFUL object (a live MAVLink/mock/
replay connection, a rolling validation history, a running EKF) built
ONCE before the loop starts and reused every cycle. M4 through M12 are
still Phase 0 stubs, called the same way as before.

run_cycle() returns a CycleResult carrying every intermediate module
output (not just the final LogRecord) — the CLI here only prints a few of
them, but api/replay_session.py (Phase 1.5's replay backend) uses the
same run_cycle() against a ReplayTelemetrySource and streams the full
CycleResult to the frontend over a WebSocket, one per cycle. This is the
one function every entry point (CLI, API, tests) calls to run the
pipeline, so there is exactly one place that wires M1 through M12 together.

Run in mock mode to exercise the whole pipeline without a simulator:

    python -m orchestrator.main --mock

and watch the RawState -> StateVector -> TwinParams flow printed each
cycle (add --no-flow to silence that and keep only the module-call logs).
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from typing import Optional

from config.loader import load_config
from contracts.schemas import (
    Ack,
    Command,
    Confidence,
    Decision,
    Feasibility,
    HealthElectrical,
    HealthStructural,
    EnergyPlan,
    LogRecord,
    RawState,
    StateVector,
    TwinParams,
)

from modules.m1_telemetry import MAVLinkGateway, MockFaultConfig, MockTelemetrySource, TelemetrySource
from modules.m2_validation import SensorValidator
from modules.m3_twin_state import TwinStateEstimator
from modules.m4_electrical_rul import predictor as m4
from modules.m5_structural_shm import StructuralHealthPredictor
from modules.m6_energy import optimizer as m6
from modules.m7_uncertainty import quantifier as m7
from modules.m8_fusion import feasibility as m8
from modules.m9_decision import engine as m9
from modules.m10_safety import arbiter as m10
from modules.m11_actuation import actuator as m11
from modules.m12_logging import logger as m12

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("hea_dt.orchestrator")


@dataclass
class CycleResult:
    """Every module's output for a single pipeline cycle. run_cycle()'s
    real return value — the CLI only prints a summary of it, but the
    replay API (api/replay_session.py) serializes the whole thing and
    streams it to the frontend so every panel can render live."""

    cycle: int
    raw: RawState
    state: StateVector
    twin: TwinParams
    health_elec: HealthElectrical
    health_struct: HealthStructural
    energy_plan: EnergyPlan
    confidence: Confidence
    feasibility: Feasibility
    decision: Decision
    command: Optional[Command]
    ack: Optional[Ack]
    log_record: LogRecord


def build_telemetry_source(config, mock: bool) -> TelemetrySource:
    """Construct M1's telemetry source: a MockTelemetrySource (with fault
    injection from config.mock) in --mock mode, or a real MAVLinkGateway
    pointed at config.sim.connection otherwise. (ReplayTelemetrySource is
    built separately by api/replay_session.py, which needs an uploaded
    file path rather than a config-driven connection string.)"""
    if mock:
        faults = MockFaultConfig(
            battery_decline_v_per_s=config.mock.battery_decline_v_per_s,
            vibration_noise_std=config.mock.vibration_noise_std,
            glitch_prob=config.mock.glitch_prob,
            glitch_field=config.mock.glitch_field,
        )
        return MockTelemetrySource(seed=config.mock.seed, faults=faults)
    return MAVLinkGateway(connection_string=config.sim.connection)


def _print_flow(
    cycle: int,
    raw: RawState,
    state: StateVector,
    twin: TwinParams,
    health_elec: Optional[HealthElectrical] = None,
    health_struct: Optional[HealthStructural] = None,
    energy_plan: Optional[EnergyPlan] = None,
    confidence: Optional[Confidence] = None,
) -> None:
    """Pretty-print one cycle's RawState -> StateVector -> TwinParams ->
    HealthElectrical/HealthStructural/EnergyPlan -> Confidence, so the
    whole sensing + predictive-intelligence flow is visible to a human,
    not just buried in logs."""
    failed_checks = [k for k, v in state.validity.items() if not v]
    print(f"\n===== cycle {cycle}: RawState -> StateVector -> TwinParams -> ... =====")
    print(
        f"  RawState      battery={raw.battery_voltage_v:.2f}V @ {raw.battery_current_a:.1f}A  "
        f"soc={raw.battery_remaining_pct:.1f}%  accel_z={raw.accel_xyz[2]:.2f}m/s^2  "
        f"gps_fix={raw.gps_fix_type} sats={raw.gps_satellites_visible}"
    )
    print(f"  StateVector   sensor_fault_flags={state.sensor_fault_flags}  failed_checks={failed_checks}")
    print(
        f"  TwinParams    mass={twin.effective_mass_kg:.3f}kg  "
        f"R_batt={twin.battery_internal_resistance_ohm:.4f}ohm  "
        f"motor_eff={['%.2f' % e for e in twin.motor_thrust_efficiency]}  "
        f"deviation={twin.deviation:.3f}"
    )
    if health_elec is not None:
        print(
            f"  HealthElectrical  battery_soh={health_elec.battery_soh_pct:.1f}%  "
            f"battery_rul={health_elec.battery_rul_s:.0f}s  "
            f"motor_health={['%.2f' % h for h in health_elec.motor_health]}  "
            f"p_fail={health_elec.p_fail_horizon:.3f}"
        )
    if health_struct is not None:
        print(
            f"  HealthStructural  imbalance_flag={health_struct.imbalance_flag}  "
            f"anomaly_score={health_struct.structural_anomaly_score:.3f}  "
            f"damage={health_struct.prop_damage_score:.2f}  "
            f"per_rotor={['%.2f' % s for s in health_struct.per_rotor_scores]}"
        )
    if energy_plan is not None:
        print(
            f"  EnergyPlan    v_opt={energy_plan.v_opt_ms:.2f}m/s  alt_opt={energy_plan.alt_opt_m:.1f}m  "
            f"predicted_energy={energy_plan.predicted_energy_wh:.1f}Wh  margin={energy_plan.energy_margin_wh:.1f}Wh"
        )
    if confidence is not None:
        print(
            f"  Confidence    per_prediction={{{', '.join(f'{k}={v:.2f}' for k, v in confidence.per_prediction.items())}}}  "
            f"decision_confidence={confidence.decision_confidence:.3f}"
        )


def run_cycle(
    config,
    telemetry_source: TelemetrySource,
    validator: SensorValidator,
    estimator: TwinStateEstimator,
    structural_predictor: StructuralHealthPredictor,
    cycle: int = 0,
    print_flow: bool = False,
) -> CycleResult:
    """Run exactly one pass through M1 -> M12 and return every module's
    output for that cycle as a CycleResult."""
    raw = telemetry_source.get_raw_state()
    state = validator.validate(raw)
    twin = estimator.estimate(state)

    # M4, M5, M6 are independent given (state, twin) — the real system runs
    # them in parallel; here they're just called in sequence since they're
    # all lightweight (tree models / closed-form physics).
    health_elec = m4.predict(state, twin, config)
    health_struct = structural_predictor.predict(state, twin)
    energy_plan = m6.optimize(state, twin, config)

    # M7 needs the EXACT feature vector M5 used (HealthStructural alone
    # only carries output scores, not the window-derived spectral
    # features) — see quantifier.py's docstring.
    confidence = m7.quantify(
        health_elec, health_struct, energy_plan, twin, state, config,
        structural_feature_row=structural_predictor.last_feature_row,
    )

    if print_flow:
        _print_flow(cycle, raw, state, twin, health_elec, health_struct, energy_plan, confidence)

    feasibility = m8.assess(health_elec, health_struct, energy_plan, confidence, twin, config)
    decision = m9.decide(feasibility, confidence, config)
    command = m10.arbitrate(decision, confidence, config)
    # Only a real MAVLinkGateway has a live drone to actuate; both
    # MockTelemetrySource and ReplayTelemetrySource represent "no live
    # autopilot attached" (fake data / historical replay respectively),
    # so M11's stub should fabricate an ack in both cases rather than
    # attempting (and failing) a real MAVLink send.
    no_live_drone = not isinstance(telemetry_source, MAVLinkGateway)
    ack = m11.actuate(command, config, mock=no_live_drone) if command is not None else None
    log_record = m12.log_decision(decision, command, ack, config)

    return CycleResult(
        cycle=cycle,
        raw=raw,
        state=state,
        twin=twin,
        health_elec=health_elec,
        health_struct=health_struct,
        energy_plan=energy_plan,
        confidence=confidence,
        feasibility=feasibility,
        decision=decision,
        command=command,
        ack=ack,
        log_record=log_record,
    )


def run_loop(config, mock: bool, cycles: int, print_flow: bool) -> None:
    """Build Layer A's and M5's stateful objects once, then run `cycles`
    passes through the full pipeline at config.loop.slow_hz."""
    telemetry_source = build_telemetry_source(config, mock)
    validator = SensorValidator(config)
    estimator = TwinStateEstimator(config)
    structural_predictor = StructuralHealthPredictor(config)

    period_s = 1.0 / config.loop.slow_hz if config.loop.slow_hz > 0 else 1.0

    for i in range(cycles):
        log.info("--- cycle %d/%d ---", i + 1, cycles)
        t0 = time.time()
        result = run_cycle(
            config, telemetry_source, validator, estimator, structural_predictor,
            cycle=i + 1, print_flow=print_flow,
        )
        log.info(
            "cycle %d result: decision=%s command=%s ack_accepted=%s",
            i + 1,
            result.decision.kind.value,
            result.command.kind.value if result.command else None,
            result.ack.accepted if result.ack else None,
        )
        elapsed = time.time() - t0
        sleep_for = max(0.0, period_s - elapsed)
        if i < cycles - 1:
            time.sleep(sleep_for)


def main() -> None:
    parser = argparse.ArgumentParser(description="HEA-DT orchestrator")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run the full pipeline with fake/random data instead of a real "
        "simulator connection. Use this to test module wiring without "
        "Gazebo/SITL running.",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to the config YAML file.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=5,
        help="Number of pipeline cycles to run before exiting.",
    )
    parser.add_argument(
        "--no-flow",
        action="store_true",
        help="Suppress the per-cycle RawState -> StateVector -> TwinParams printout.",
    )
    args = parser.parse_args()

    if not args.mock:
        log.warning(
            "Running without --mock: M1 will attempt a real MAVLink "
            "connection (config.sim.connection), which will raise "
            "ConnectionError if no SITL is reachable."
        )

    config = load_config(args.config)
    log.info("Loaded config from %s", args.config)
    log.info(
        "sim.backend=%s sim.connection=%s autonomy.mode=%s",
        config.sim.backend,
        config.sim.connection,
        config.autonomy.mode,
    )

    run_loop(config, mock=args.mock, cycles=args.cycles, print_flow=not args.no_flow)
    log.info("Done — %d cycle(s) completed.", args.cycles)


if __name__ == "__main__":
    main()
