"""
modules/m1_telemetry/base.py

Shared interface for anything that can act as M1's telemetry source: the
real MAVLink gateway (gateway.py) and the mock generator (mock_source.py)
both implement this, so the orchestrator and every downstream module can
treat "read the next RawState" and "send a test command" the same way
regardless of whether a real simulator is running.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque

from contracts.schemas import Ack, RawState


class TelemetrySource(ABC):
    """Base class for M1 telemetry sources.

    Subclasses must implement get_raw_state() (the read side) and
    send_test_command() (the write side — proves the channel is
    bidirectional from Phase 1 onward, per the project's core design
    principle of closing the loop rather than only ever reading
    telemetry).
    """

    def __init__(self, ring_buffer_size: int = 200):
        self.ring_buffer: deque[RawState] = deque(maxlen=ring_buffer_size)

    @abstractmethod
    def get_raw_state(self) -> RawState:
        """Return the latest telemetry snapshot as a RawState."""

    @abstractmethod
    def send_test_command(self, speed_ms: float) -> Ack:
        """Send a MAV_CMD_DO_CHANGE_SPEED test command and return its Ack.

        This exists to prove the write side of the gateway works from
        Phase 1 onward — M11 (Phase 4) will build the full sanctioned-
        Command pipeline on top of the same underlying connection this
        method uses.
        """

    def recent(self, n: int = 10) -> list[RawState]:
        """Return the last n RawState readings (oldest first)."""
        return list(self.ring_buffer)[-n:]

    def _record(self, state: RawState) -> RawState:
        """Append a reading to the ring buffer and return it (helper for subclasses)."""
        self.ring_buffer.append(state)
        return state
