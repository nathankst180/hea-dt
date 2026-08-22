"""
M9 — Adaptive Mission Decision Engine (stub)

Real implementation (Phase 4) will be an explicit, defensible finite-state
machine: NOMINAL if Feasibility.feasible and nothing needs changing,
REOPTIMIZE if feasible but EnergyPlan has a better route, REDUCE_SCOPE if
margins are tight, ABORT if config.thresholds.p_fail_abort or
structural_abort is crossed — with hysteresis so it doesn't oscillate
between states cycle to cycle.

For now this stub always decides NOMINAL, so M10 has a well-typed Decision
to arbitrate.
"""

from __future__ import annotations

import logging
import time

from contracts.schemas import Confidence, Decision, DecisionKind, Feasibility

logger = logging.getLogger("hea_dt.m9_decision")


def decide(feasibility: Feasibility, confidence: Confidence, config) -> Decision:
    """Choose the graded mission action for this cycle.

    Args:
        feasibility: M8's output.
        confidence: M7's output.
        config: the loaded Config object (config.thresholds.* define the
            abort/reduce-scope trigger points once implemented).
    """
    logger.info("M9 called")

    return Decision(
        timestamp=time.time(),
        kind=DecisionKind.NOMINAL,
        params={},
        triggered_by="stub: always NOMINAL until M9 is implemented",
    )
