"""
M11 — Closed-Loop Command & Actuation (stub)

Real implementation (Phase 4 — THE CORE NOVELTY, protect this above all
else) will translate a sanctioned Command into real MAVLink messages sent
back through M1's gateway connection: MISSION_CLEAR_ALL/MISSION_ITEM_INT
for REROUTE/REDUCE_SCOPE, SET_POSITION_TARGET_GLOBAL_INT for guided
setpoints, MAV_CMD_DO_CHANGE_SPEED for SET_SPEED, MAV_CMD_NAV_RETURN_TO_
LAUNCH/MAV_CMD_NAV_LAND for abort — with ACK/timeout/retry handling and
write-back-latency measurement (the RQ4 metric).

For now this stub just logs the command it "would" send and fabricates an
immediate, accepted Ack, so M12 has a well-typed object to log and the
whole pipeline can be exercised end-to-end without a simulator running.
"""

from __future__ import annotations

import logging
import time

from contracts.schemas import Ack, Command

logger = logging.getLogger("hea_dt.m11_actuation")


def actuate(command: Command, config, mock: bool = True) -> Ack:
    """Send a sanctioned Command to the autopilot and return its Ack.

    Args:
        command: M10's sanctioned output.
        config: the loaded Config object (config.sim.connection used in
            live mode, shared with M1's gateway).
        mock: if True, fabricate an immediate accepted Ack instead of
            sending anything over MAVLink.
    """
    logger.info("M11 called (mock=%s) — would send %s %s", mock, command.kind.value, command.payload)

    if not mock:
        raise NotImplementedError(
            "M11 live MAVLink command send is not implemented yet — "
            "run the orchestrator with --mock, or implement this branch "
            "using the same connection M1 opened."
        )

    t0 = time.time()
    # Stubbed "send" — a real implementation measures actual round-trip time here.
    latency_ms = (time.time() - t0) * 1000.0

    return Ack(
        timestamp=time.time(),
        accepted=True,
        latency_ms=latency_ms,
        detail="mock ack: command not actually sent (M11 not yet implemented)",
    )
