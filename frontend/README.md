# HEA-DT Frontend

A React + TypeScript + Vite dashboard for replaying uploaded flight telemetry
through the real HEA-DT backend pipeline and watching every module's output
live: a 3D digital twin, health/energy predictions, the closed-loop
decision, and the hash-chained decision log.

This is a separate project from the Python backend (`../hea-dt`) — its own
`package.json`, its own dependency tree, talking to the backend only over
HTTP/WebSocket (see `src/api/client.ts`).

## Stack

- **React 19 + TypeScript + Vite** — app shell and build tooling.
- **react-three-fiber + drei** — the 3D scene (Panel 1). The drone model is
  built entirely from primitive geometry (`src/components/three/DroneModel.tsx`)
  — no external `.glb`/asset files.
- **Recharts** — the vibration spectrum and energy comparison charts (Panel 2).
- **Tailwind CSS v4** — styling (dark "mission control" theme, see `src/index.css`).
- **Zustand** — the one shared store (`src/store/useReplayStore.ts`) every
  panel reads from, so the 3D view, predictions, closed-loop view, decision
  log, and timeline scrubber are always showing the same instant.

## Running

```bash
npm install
npm run dev
```

Opens on **http://localhost:5173**. It expects the backend API running on
**http://127.0.0.1:8000** (see `../hea-dt/README.md`, or the root `README.md`
for running both together). To point at a different backend URL, copy
`.env.example` to `.env.local` and set `VITE_API_BASE_URL`.

Type-check / production build:

```bash
npx tsc -b        # type-check only
npm run build     # type-check + production bundle (dist/)
```

## How data flows

```
UploadScreen --POST /upload--> backend, gets session_id
     |
     v
UploadScreen --POST /replay/start--> backend starts replaying that session
     |
     v
Dashboard mounts --WS /ws/state?session_id=...--> backend streams one
     |                                              CycleSnapshot per cycle
     v
useReplayWebSocket appends each snapshot to useReplayStore
     |
     v
All 4 panels + TimelineScrubber read from useReplayStore — nothing panel-
local; the store's `playheadIndex` is what "scrubbing" moves.
```

`useReplayStatusPolling` separately polls `GET /replay/status` (the
WebSocket only carries per-cycle data, not session lifecycle like
RUNNING -> FINISHED).

## Structure

```
src/
  types.ts              TS types mirroring the backend's JSON (contracts/schemas.py)
  constants.ts           Shared decision-kind color/label mapping
  api/client.ts            REST calls: upload, replay start/stop, status, logs
  store/useReplayStore.ts   The single source of truth (snapshots, playhead, session)
  hooks/                     useReplayWebSocket, useReplayStatusPolling
  utils/                      geo.ts (lat/lon -> scene position), dft.ts (vibration
                               spectrum), format.ts
  components/
    TopNav.tsx                 App header + the "Live" (disabled) mode placeholder
    UploadScreen.tsx             Drag-and-drop upload + speed selector + Start Replay
    Dashboard.tsx                  Wires the WS/polling hooks, lays out the 4 panels
    PanelFrame.tsx                  Shared panel chrome (title bar)
    TimelineScrubber.tsx             The shared timeline at the bottom
    three/Scene.tsx, DroneModel.tsx   The 3D scene
    panels/
      DigitalTwinPanel.tsx            Panel 1 — M1-M3
      PredictionsPanel.tsx             Panel 2 — M4-M7
      ClosedLoopPanel.tsx               Panel 3 — M8-M11
      DecisionLogPanel.tsx               Panel 4 — M12
```

## Current limitations (honest, not hidden)

- **M4-M12 are still backend stubs** (see the main project's phase plan) — so
  Panels 2 and 3 will show static "fully healthy / NOMINAL" values until
  those modules are actually implemented. The panels themselves are fully
  wired to real data; there's just nothing interesting for them to show yet.
  Panel 1 (position/attitude/battery/deviation) and the vibration spectrum
  in Panel 2 ARE real, computed from the actual replayed telemetry.
- **"Live" mode is a placeholder.** The tab exists in `TopNav.tsx` (disabled,
  labeled "Soon") so the UI slot is ready, but streaming directly from a
  running PX4/ArduPilot SITL instance isn't built yet — only upload+replay is.
- **Scrubbing range is bounded by what's arrived.** You can seek back to any
  point already streamed, but not ahead of the live edge — the same
  constraint a live video stream has. Once replay finishes, the whole flight
  is scrubbable.
