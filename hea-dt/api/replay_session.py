"""
api/replay_session.py

ReplaySession runs one uploaded telemetry file through the REAL M1-M12
pipeline in a background thread — orchestrator.main.run_cycle() underneath,
the exact same function a live MAVLink connection would use — and
broadcasts each cycle's full result to any connected WebSocket clients.

A background thread (not an asyncio task) runs the pipeline because it's
synchronous and blocking: the EKF math blocks, and ReplayTelemetrySource's
pacing (time.sleep, so a 2-minute flight replays over roughly 2 minutes)
blocks. Running that directly on the asyncio event loop would freeze every
other request the API is trying to serve. Results cross back into the
event loop via loop.call_soon_threadsafe()/run_coroutine_threadsafe(), the
standard pattern for a worker thread to talk to asyncio code.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from enum import Enum
from pathlib import Path
from typing import Optional

from fastapi import WebSocket

from api.serialization import cycle_result_to_dict
from config.loader import Config
from modules.m1_telemetry.replay_source import ReplayTelemetrySource
from modules.m2_validation import SensorValidator
from modules.m3_twin_state import TwinStateEstimator
from modules.m5_structural_shm import StructuralHealthPredictor
from orchestrator.main import run_cycle

logger = logging.getLogger("hea_dt.api.replay_session")


class SessionStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FINISHED = "FINISHED"
    ERROR = "ERROR"


class ReplaySession:
    """One uploaded file's replay state: the file path, the background
    thread that's replaying it (if any), every cycle produced so far
    (kept in memory so a client that connects mid-replay, or reconnects,
    still gets the full history), and the set of WebSocket subscribers to
    push new cycles to as they're produced."""

    def __init__(self, session_id: str, file_path: Path, config: Config):
        self.session_id = session_id
        self.file_path = file_path
        self.config = config
        self.status = SessionStatus.IDLE
        self.error_detail: Optional[str] = None

        self._snapshots: list[dict] = []
        self._subscribers: list[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # ---- control (called from async request handlers) --------------------

    def start(self, speed_multiplier: float = 1.0) -> None:
        if self.status == SessionStatus.RUNNING:
            return  # already running; ignore a duplicate start request
        self._stop_event.clear()
        with self._lock:
            self._snapshots = []
        self.status = SessionStatus.RUNNING
        self.error_detail = None
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._run, args=(speed_multiplier,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ---- background thread: the actual pipeline run ----------------------

    def _run(self, speed_multiplier: float) -> None:
        try:
            telemetry_source = ReplayTelemetrySource(self.file_path, speed_multiplier=speed_multiplier)
            validator = SensorValidator(self.config)
            estimator = TwinStateEstimator(self.config)
            structural_predictor = StructuralHealthPredictor(self.config)

            cycle = 0
            while telemetry_source.has_next():
                if self._stop_event.is_set():
                    self.status = SessionStatus.STOPPED
                    return
                cycle += 1
                result = run_cycle(
                    self.config, telemetry_source, validator, estimator, structural_predictor, cycle=cycle
                )
                snapshot = cycle_result_to_dict(result)
                with self._lock:
                    self._snapshots.append(snapshot)
                self._broadcast(snapshot)

            self.status = SessionStatus.FINISHED
        except Exception as exc:  # a replay bug must not take the whole API down
            logger.exception("replay session %s failed", self.session_id)
            self.status = SessionStatus.ERROR
            self.error_detail = str(exc)

    def _broadcast(self, snapshot: dict) -> None:
        if self._loop is None:
            return
        for websocket in list(self._subscribers):
            asyncio.run_coroutine_threadsafe(_safe_send(websocket, snapshot), self._loop)

    # ---- subscribers (called from the WS endpoint, on the event loop) ----

    async def subscribe(self, websocket: WebSocket) -> None:
        self._subscribers.append(websocket)
        # Backfill everything produced so far, so a client that connects
        # mid-replay (or after it finished) still sees the full history —
        # not just cycles produced after it happened to connect.
        with self._lock:
            backlog = list(self._snapshots)
        for snapshot in backlog:
            await _safe_send(websocket, snapshot)

    def unsubscribe(self, websocket: WebSocket) -> None:
        if websocket in self._subscribers:
            self._subscribers.remove(websocket)

    # ---- read-only accessors (GET /logs, GET /replay/status) -------------

    def log_records(self) -> list[dict]:
        with self._lock:
            return [s["log_record"] for s in self._snapshots]

    def status_dict(self) -> dict:
        with self._lock:
            produced = len(self._snapshots)
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "cycles_produced": produced,
            "error_detail": self.error_detail,
        }


async def _safe_send(websocket: WebSocket, snapshot: dict) -> None:
    try:
        await websocket.send_json(snapshot)
    except Exception:
        pass  # client likely disconnected; the WS endpoint will unsubscribe it
