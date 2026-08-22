"""
api/serialization.py

Converts a CycleResult (orchestrator/main.py — every module's output for
one pipeline cycle) into a plain, JSON-safe dict for the WebSocket stream
and the /logs endpoint.

This is needed because contracts/schemas.py's dataclasses aren't directly
JSON-serializable: they contain Enum members (DecisionKind, CommandKind)
and occasionally float('inf') (e.g. HealthElectrical.battery_rul_s when a
battery is reported fully healthy) — neither survives a plain json.dumps().
"""

from __future__ import annotations

import dataclasses
import math
from enum import Enum
from typing import Any


def _sanitize(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _sanitize(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


def cycle_result_to_dict(result) -> dict:
    """result: an orchestrator.main.CycleResult. Every module's output for
    one cycle, as a flat dict of JSON-safe sub-dicts — one key per module,
    matching the naming the frontend expects (see frontend/src/types.ts)."""
    return {
        "cycle": result.cycle,
        "raw_state": _sanitize(result.raw),
        "state_vector": _sanitize(result.state),
        "twin_params": _sanitize(result.twin),
        "health_electrical": _sanitize(result.health_elec),
        "health_structural": _sanitize(result.health_struct),
        "energy_plan": _sanitize(result.energy_plan),
        "confidence": _sanitize(result.confidence),
        "feasibility": _sanitize(result.feasibility),
        "decision": _sanitize(result.decision),
        "command": _sanitize(result.command),
        "ack": _sanitize(result.ack),
        "log_record": _sanitize(result.log_record),
    }
