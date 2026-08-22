// PANEL 1 — Live Digital Twin (M1-M3). The 3D scene, driven entirely by
// replayed telemetry: real position/attitude from RawState, real
// battery/deviation from TwinParams — nothing here is faked animation.

import { useMemo } from "react";
import { Scene } from "../three/Scene";
import { useReplayStore } from "../../store/useReplayStore";
import { llaToScenePosition } from "../../utils/geo";

const MAX_TRAIL_POINTS = 400;

export function DigitalTwinPanel() {
  const snapshots = useReplayStore((s) => s.snapshots);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);

  const current = playheadIndex >= 0 ? snapshots[playheadIndex] : null;

  const firstSnapshot = snapshots[0];
  const origin = useMemo(() => {
    if (!firstSnapshot) return null;
    return { lat: firstSnapshot.raw_state.position_lla[0], lon: firstSnapshot.raw_state.position_lla[1] };
  }, [firstSnapshot]);

  const dronePosition = useMemo<[number, number, number]>(() => {
    if (!current || !origin) return [0, 0, 0];
    const [lat, lon, alt] = current.raw_state.position_lla;
    return llaToScenePosition(lat, lon, alt, origin.lat, origin.lon);
  }, [current, origin]);

  const droneRotation = useMemo<[number, number, number]>(() => {
    if (!current) return [0, 0, 0];
    const [roll, pitch, yaw] = current.raw_state.attitude_rpy;
    // Body NED (roll about forward axis, pitch about right axis, yaw
    // about down axis) mapped onto the scene's X=East/Y=Up/Z=-North axes.
    return [-pitch, -yaw, -roll];
  }, [current]);

  const trailPoints = useMemo<[number, number, number][]>(() => {
    if (!origin || playheadIndex < 0) return [];
    const upTo = snapshots.slice(0, playheadIndex + 1);
    const windowed = upTo.length > MAX_TRAIL_POINTS ? upTo.slice(upTo.length - MAX_TRAIL_POINTS) : upTo;
    return windowed.map((snap) => {
      const [lat, lon, alt] = snap.raw_state.position_lla;
      return llaToScenePosition(lat, lon, alt, origin.lat, origin.lon);
    });
  }, [snapshots, playheadIndex, origin]);

  if (!current) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-faint)]">
        Waiting for telemetry…
      </div>
    );
  }

  return (
    <div className="relative h-full">
      <Scene
        dronePosition={dronePosition}
        droneRotation={droneRotation}
        armed={current.raw_state.armed}
        motorRpm={current.raw_state.motor_rpm}
        batteryPct={current.raw_state.battery_remaining_pct}
        deviation={current.twin_params.deviation}
        faulted={current.state_vector.sensor_fault_flags.length > 0}
        trailPoints={trailPoints}
      />
      <div className="pointer-events-none absolute left-3 top-3 flex flex-col gap-1">
        <span className="rounded bg-black/40 px-2 py-1 text-[11px] text-[var(--color-text-muted)]">
          {current.raw_state.flight_mode || "—"} &middot; alt {current.raw_state.position_lla[2].toFixed(1)}m
        </span>
        {current.state_vector.sensor_fault_flags.length > 0 && (
          <span className="rounded bg-[var(--color-danger)]/20 px-2 py-1 text-[11px] text-[var(--color-danger)]">
            sensor fault: {current.state_vector.sensor_fault_flags.join(", ")}
          </span>
        )}
      </div>
    </div>
  );
}
