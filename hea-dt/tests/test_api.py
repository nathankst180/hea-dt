"""End-to-end tests for the FastAPI replay backend (api/server.py):
upload -> replay/start -> WebSocket stream -> replay/status -> logs. Uses
a tiny synthetic CSV with millisecond-scale timestamp gaps at a high speed
multiplier so the whole replay finishes in well under a second."""

import io
import time

import pandas as pd
from fastapi.testclient import TestClient

from api.server import app


def _tiny_csv_bytes(n_rows: int = 5, dt: float = 0.01) -> bytes:
    rows = []
    for i in range(n_rows):
        rows.append(
            {
                "timestamp": i * dt,
                "battery_voltage_v": 16.0 - 0.01 * i,
                "battery_current_a": 10.0,
                "battery_remaining_pct": 90.0 - 0.1 * i,
                "gps_fix_type": 3,
                "gps_satellites_visible": 12,
                "motor_pwm_1": 1500.0,
                "motor_rpm_1": 5000.0,
                "accel_xyz_x": 0.0,
                "accel_xyz_y": 0.0,
                "accel_xyz_z": 9.81,
                "position_lat": 47.4,
                "position_lon": 8.5,
                "position_alt_m": 80.0,
            }
        )
    buf = io.BytesIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue()


def test_upload_returns_session_id_and_summary():
    with TestClient(app) as client:
        response = client.post(
            "/upload",
            files={"file": ("flight.csv", _tiny_csv_bytes(5, dt=0.01), "text/csv")},
        )
    assert response.status_code == 200
    body = response.json()
    assert "session_id" in body
    assert body["num_rows"] == 5
    assert abs(body["duration_s"] - 0.04) < 1e-6


def test_upload_rejects_bad_extension():
    with TestClient(app) as client:
        response = client.post(
            "/upload",
            files={"file": ("flight.txt", b"not telemetry", "text/plain")},
        )
    assert response.status_code == 400


def test_upload_rejects_missing_timestamp_column():
    buf = io.BytesIO()
    pd.DataFrame([{"battery_voltage_v": 16.0}]).to_csv(buf, index=False)
    with TestClient(app) as client:
        response = client.post(
            "/upload",
            files={"file": ("flight.csv", buf.getvalue(), "text/csv")},
        )
    assert response.status_code == 400
    assert "timestamp" in response.json()["detail"]


def test_replay_start_unknown_session_returns_404():
    with TestClient(app) as client:
        response = client.post("/replay/start", json={"session_id": "does-not-exist", "speed_multiplier": 10.0})
    assert response.status_code == 404


def test_full_replay_flow_upload_start_stream_logs():
    with TestClient(app) as client:
        upload = client.post(
            "/upload",
            files={"file": ("flight.csv", _tiny_csv_bytes(6, dt=0.01), "text/csv")},
        )
        session_id = upload.json()["session_id"]

        start = client.post("/replay/start", json={"session_id": session_id, "speed_multiplier": 100.0})
        assert start.status_code == 200

        received = []
        with client.websocket_connect(f"/ws/state?session_id={session_id}") as ws:
            for _ in range(6):
                received.append(ws.receive_json())

        assert len(received) == 6
        # cycles arrive in order, 1..6
        assert [s["cycle"] for s in received] == [1, 2, 3, 4, 5, 6]

        # every module's output is present on each streamed snapshot
        first = received[0]
        for key in (
            "raw_state",
            "state_vector",
            "twin_params",
            "health_electrical",
            "health_structural",
            "energy_plan",
            "confidence",
            "feasibility",
            "decision",
            "command",
            "ack",
            "log_record",
        ):
            assert key in first, f"missing {key} in streamed snapshot"

        assert first["raw_state"]["battery_voltage_v"] == 16.0
        assert first["decision"]["kind"] == "NOMINAL"  # M9 is still a Phase 0 stub
        assert first["log_record"]["record_hash"] != ""

        # wait for the background thread to fully finish before polling status
        for _ in range(50):
            status = client.get("/replay/status", params={"session_id": session_id}).json()
            if status["status"] == "FINISHED":
                break
            time.sleep(0.05)
        assert status["status"] == "FINISHED"
        assert status["cycles_produced"] == 6

        logs = client.get("/logs", params={"session_id": session_id})
        assert logs.status_code == 200
        assert len(logs.json()["records"]) == 6


def test_websocket_backfills_history_for_late_subscriber():
    """A client that connects AFTER replay has already produced some
    cycles should still receive the full history, not just new cycles."""
    with TestClient(app) as client:
        upload = client.post(
            "/upload",
            files={"file": ("flight.csv", _tiny_csv_bytes(4, dt=0.01), "text/csv")},
        )
        session_id = upload.json()["session_id"]

        client.post("/replay/start", json={"session_id": session_id, "speed_multiplier": 100.0})

        # give the background thread time to finish before connecting
        for _ in range(50):
            status = client.get("/replay/status", params={"session_id": session_id}).json()
            if status["status"] == "FINISHED":
                break
            time.sleep(0.05)

        with client.websocket_connect(f"/ws/state?session_id={session_id}") as ws:
            received = [ws.receive_json() for _ in range(4)]

        assert [s["cycle"] for s in received] == [1, 2, 3, 4]


def test_replay_stop_halts_before_completion():
    with TestClient(app) as client:
        # 400 rows, 0.05s apart -> 20s of recorded time; at 1x speed this
        # would take ~20s to finish, giving us plenty of time to stop it
        # mid-flight. The sleep below is generous because SensorValidator's
        # IsolationForest fit (~0.2s) happens inside the background thread
        # before the first cycle is even produced.
        upload = client.post(
            "/upload",
            files={"file": ("flight.csv", _tiny_csv_bytes(400, dt=0.05), "text/csv")},
        )
        session_id = upload.json()["session_id"]

        client.post("/replay/start", json={"session_id": session_id, "speed_multiplier": 1.0})
        time.sleep(1.0)
        client.post("/replay/stop", json={"session_id": session_id})

        for _ in range(50):
            status = client.get("/replay/status", params={"session_id": session_id}).json()
            if status["status"] == "STOPPED":
                break
            time.sleep(0.05)

        assert status["status"] == "STOPPED"
        assert 0 < status["cycles_produced"] < 400
