"""
api/server.py

Lightweight FastAPI backend for the HEA-DT replay dashboard. Exposes:

  POST /upload           - upload a telemetry file (CSV/Parquet), get a session_id back
  POST /replay/start      - start replaying an uploaded session through the REAL M1-M12 pipeline
  POST /replay/stop        - stop an in-progress replay
  GET  /replay/status       - poll a session's replay progress
  GET  /logs                  - pull M12's decision log for a session
  WS   /ws/state                - live stream of every module's output, one message per cycle

This is a thin transport layer: all the actual work (validation, EKF,
decision-making, ...) happens in modules/ and orchestrator/ exactly as it
would for a live MAVLink connection — see api/replay_session.py, which
calls the same orchestrator.main.run_cycle() the CLI uses.

Run with:
    uvicorn api.server:app --reload --port 8000
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.replay_session import ReplaySession
from config.loader import load_config
from modules.m1_telemetry.replay_source import TelemetryFormatError, load_telemetry_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hea_dt.api")

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="HEA-DT API", description="Replay uploaded telemetry through the real HEA-DT pipeline.")

# The Vite dev server runs on 5173 by default; allow both localhost and
# 127.0.0.1 since browsers treat them as different origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_config = load_config("config/config.yaml")
_sessions: dict[str, ReplaySession] = {}


def _get_session(session_id: str) -> ReplaySession:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(404, f"unknown session_id {session_id!r} — upload a file first")
    return session


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    num_rows: int
    duration_s: float
    start_time: float
    end_time: float


@app.post("/upload", response_model=UploadResponse)
async def upload_telemetry(file: UploadFile = File(...)) -> UploadResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".parquet"):
        raise HTTPException(400, f"unsupported file type {suffix!r} — expected .csv or .parquet")

    session_id = str(uuid.uuid4())
    dest = UPLOAD_DIR / f"{session_id}{suffix}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        rows = load_telemetry_file(dest)
    except TelemetryFormatError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # malformed CSV/parquet, wrong dtypes, etc.
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"could not parse telemetry file: {exc}") from exc

    session = ReplaySession(session_id=session_id, file_path=dest, config=_config)
    _sessions[session_id] = session
    logger.info("uploaded session %s: %s (%d rows)", session_id, file.filename, len(rows))

    return UploadResponse(
        session_id=session_id,
        filename=file.filename or dest.name,
        num_rows=len(rows),
        duration_s=rows[-1].timestamp - rows[0].timestamp if len(rows) > 1 else 0.0,
        start_time=rows[0].timestamp,
        end_time=rows[-1].timestamp,
    )


# ---------------------------------------------------------------------------
# POST /replay/start, POST /replay/stop, GET /replay/status
# ---------------------------------------------------------------------------


class ReplayStartRequest(BaseModel):
    session_id: str
    speed_multiplier: float = 1.0


@app.post("/replay/start")
async def start_replay(req: ReplayStartRequest) -> dict:
    session = _get_session(req.session_id)
    session.start(speed_multiplier=req.speed_multiplier)
    return {"status": "started", "session_id": req.session_id, "speed_multiplier": req.speed_multiplier}


class ReplayStopRequest(BaseModel):
    session_id: str


@app.post("/replay/stop")
async def stop_replay(req: ReplayStopRequest) -> dict:
    session = _get_session(req.session_id)
    session.stop()
    return {"status": "stopping", "session_id": req.session_id}


@app.get("/replay/status")
async def replay_status(session_id: str) -> dict:
    return _get_session(session_id).status_dict()


# ---------------------------------------------------------------------------
# GET /logs
# ---------------------------------------------------------------------------


@app.get("/logs")
async def get_logs(session_id: str) -> dict:
    session = _get_session(session_id)
    return {"session_id": session_id, "records": session.log_records()}


# ---------------------------------------------------------------------------
# WS /ws/state
# ---------------------------------------------------------------------------


@app.websocket("/ws/state")
async def ws_state(websocket: WebSocket, session_id: str) -> None:
    session = _sessions.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason="unknown session_id")
        return

    await websocket.accept()
    await session.subscribe(websocket)
    try:
        while True:
            # We don't expect messages from the client, but awaiting one
            # lets us detect a client-initiated disconnect promptly rather
            # than only on the next broadcast attempt.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        session.unsubscribe(websocket)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "sessions": len(_sessions)}
