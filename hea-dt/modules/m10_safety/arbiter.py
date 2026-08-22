"""
M10 — Safety Arbitration & Bounded Autonomy (stub)

Real implementation (Phase 4) will check a Decision against the safety
envelope (config.thresholds.min_altitude_m, battery reserve, geofence),
apply reversibility gating (irreversible actions need a human under
config.autonomy.mode == "human_in_the_loop") and confidence gating
(escalate if confidence.decision_confidence < config.thresholds.
min_decision_confidence), then either sanction a Command or return None to
signal "awaiting human confirmation."

For now this stub always translates a NOMINAL decision straight into a
reversible SET_SPEED Command, so M11 has something well-typed to actuate.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from contracts.schemas import Command, CommandKind, Confidence, Decision, DecisionKind

logger = logging.getLogger("hea_dt.m10_safety")


def arbitrate(decision: Decision, confidence: Confidence, config) -> Optional[Command]:
    """Check a Decision against the safety envelope and either sanction a
    Command or withhold one pending human confirmation.

    Args:
        decision: M9's output.
        confidence: M7's output (used for confidence gating once implemented).
        config: the loaded Config object (config.thresholds.* and
            config.autonomy.mode define the real gating logic).

    Returns:
        A sanctioned Command, or None if the action is being withheld
        (e.g. pending a human).
    """
    logger.info("M10 called")

    speed = decision.params.get("v_opt_ms", 10.0) if decision.kind == DecisionKind.NOMINAL else 10.0

    return Command(
        timestamp=time.time(),
        kind=CommandKind.SET_SPEED,
        payload={"speed_ms": speed},
        reversible=True,
        requires_human=False,
    )
