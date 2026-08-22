"""
M12 — Explainability & Immutable Decision Log (stub)

Real implementation (Phase 5) will persist every LogRecord to the SQLite
file at config.paths.log_db, generate a proper human-readable rationale
(template- or SHAP-based) from the predictions that triggered the
decision, and support post-flight export for evaluation.

This stub already does the hash-chaining described in the data contract
(contracts/schemas.py::LogRecord) since it's cheap and central to the
"immutable, auditable log" design principle — but it only keeps the chain
in memory (module-level _prev_hash) and does not write to SQLite yet.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from contracts.schemas import Ack, Command, Decision, LogRecord

logger = logging.getLogger("hea_dt.m12_logging")

_prev_hash: str = ""  # in-memory chain state; real persistence lands in Phase 5


def _hash_record(prev_hash: str, decision: Decision, command: Optional[Command], ack: Optional[Ack], rationale: str) -> str:
    """SHA-256 hex digest over the record's content, chained to prev_hash."""
    content = (
        f"{prev_hash}|{decision.timestamp}|{decision.kind}|{decision.triggered_by}|"
        f"{command.kind if command else None}|{command.payload if command else None}|"
        f"{ack.accepted if ack else None}|{ack.latency_ms if ack else None}|"
        f"{rationale}"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def log_decision(decision: Decision, command: Optional[Command], ack: Optional[Ack], config) -> LogRecord:
    """Bundle a Decision + Command + Ack into a hash-chained LogRecord.

    Args:
        decision: M9's output.
        command: M10's output (None if the action was withheld pending a human).
        ack: M11's output (None if no command was sent this cycle).
        config: the loaded Config object (config.paths.log_db will be used
            once SQLite persistence is implemented).
    """
    global _prev_hash
    logger.info("M12 called")

    rationale = decision.triggered_by or "no rationale provided"
    record_hash = _hash_record(_prev_hash, decision, command, ack, rationale)

    record = LogRecord(
        timestamp=time.time(),
        decision=decision,
        command=command,
        ack=ack,
        rationale=rationale,
        prev_hash=_prev_hash,
        record_hash=record_hash,
    )

    _prev_hash = record_hash
    return record
