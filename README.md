# HEA-DT — Health- and Energy-Aware Adaptive Digital Twin

Two sibling projects:

- **[`hea-dt/`](hea-dt/README.md)** — the Python backend: the 12-module
  pipeline (M1-M12), the FastAPI replay API, config, tests. Start here for
  anything about the digital twin itself.
- **[`frontend/`](frontend/README.md)** — the React + Three.js dashboard
  that replays an uploaded flight through the backend and renders it live:
  a 3D digital twin, predictions, the closed-loop decision, and the
  decision log.

## Run both together

Two terminals, both from a fresh clone:

**Terminal 1 — backend API**

```bash
cd hea-dt
pip install -r requirements.txt
uvicorn api.server:app --reload --port 8000
```

**Terminal 2 — frontend**

```bash
cd frontend
npm install
npm run dev
```

Then open **http://localhost:5173**, upload a telemetry file (or generate
a demo one first — see below), pick a replay speed, and hit **Start
Replay**.

### Don't have a telemetry file yet?

Generate one — a short synthetic flight (takeoff, a circular cruise, a
landing) with no SITL/Gazebo required:

```bash
cd hea-dt
python scripts/generate_sample_telemetry.py    # writes data/sample_telemetry.csv
```

Upload that file through the UI once both servers are running.

## What you're actually seeing

The uploaded file is replayed through `orchestrator.run_cycle()` — the
same function a live MAVLink connection would call — so Panel 1 (the 3D
twin: position, attitude, battery, twin-deviation δ(t)) reflects **real**
computation: M2's sensor validation and M3's EKF genuinely run against the
replayed data. Panels 2 and 3 (predictions, closed-loop decision) are
correctly wired end-to-end but will show static "fully healthy / NOMINAL"
values until M4-M12 are built out past their current Phase 0 stubs — see
`hea-dt/README.md`'s build order. Panel 4 (decision log) is real:
M12 hash-chains every decision produced, one per cycle.

## Repo layout

```
digitaltwin/
  hea-dt/       Python backend — pipeline, API, tests, config, sample data
  frontend/     React dashboard — separate package.json, its own README
```
