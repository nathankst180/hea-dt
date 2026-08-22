// Shared timeline for all 4 panels. Dragging the slider scrubs the whole
// dashboard to that cycle (pauses "follow live"); dragging back to the
// latest point — or clicking "Resume live" — re-enables auto-follow.

import { useReplayStore } from "../store/useReplayStore";
import type { SessionStatus } from "../types";
import { formatDuration } from "../utils/format";

const STATUS_COLOR: Record<SessionStatus, string> = {
  IDLE: "var(--color-text-faint)",
  RUNNING: "var(--color-nominal)",
  STOPPED: "var(--color-warn)",
  FINISHED: "var(--color-accent)",
  ERROR: "var(--color-danger)",
};

export function TimelineScrubber() {
  const session = useReplayStore((s) => s.session);
  const snapshots = useReplayStore((s) => s.snapshots);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);
  const setPlayheadIndex = useReplayStore((s) => s.setPlayheadIndex);
  const isFollowingLive = useReplayStore((s) => s.isFollowingLive);
  const resumeLive = useReplayStore((s) => s.resumeLive);
  const replayStatus = useReplayStore((s) => s.replayStatus);
  const wsConnected = useReplayStore((s) => s.wsConnected);

  // The slider can only SEEK within what's already arrived (snapshots),
  // but the duration/count DISPLAYED should reflect the whole uploaded
  // flight (from the /upload response) — otherwise "0:45 / 0:45" reads as
  // "done" when replay is really only partway through a 2-minute flight.
  const maxIndex = Math.max(0, snapshots.length - 1);
  const current = playheadIndex >= 0 ? snapshots[playheadIndex] : null;
  const first = snapshots[0];

  const currentT = current && first ? current.raw_state.timestamp - first.raw_state.timestamp : 0;
  const totalT = session?.duration_s ?? 0;
  const totalRows = session?.num_rows ?? snapshots.length;

  return (
    <div className="flex shrink-0 items-center gap-4 border-t border-[var(--color-border)] bg-[var(--color-panel)] px-5 py-3">
      <div className="flex items-center gap-1.5" title={`replay: ${replayStatus} · websocket: ${wsConnected ? "connected" : "disconnected"}`}>
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: STATUS_COLOR[replayStatus], boxShadow: `0 0 6px ${STATUS_COLOR[replayStatus]}` }}
        />
        <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{replayStatus}</span>
      </div>

      <span className="mono-tabular w-24 text-xs text-[var(--color-text-muted)]">
        {formatDuration(currentT)} / {formatDuration(totalT)}
      </span>

      <input
        type="range"
        min={0}
        max={maxIndex}
        value={Math.max(0, playheadIndex)}
        disabled={snapshots.length === 0}
        onChange={(e) => setPlayheadIndex(Number(e.target.value))}
        className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-[var(--color-border)] accent-[var(--color-accent)] disabled:cursor-not-allowed"
      />

      <span className="mono-tabular w-20 text-right text-xs text-[var(--color-text-faint)]">
        cycle {current?.cycle ?? 0}/{totalRows}
      </span>

      {isFollowingLive ? (
        <span className="flex w-24 items-center gap-1.5 text-xs font-medium text-[var(--color-nominal)]">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--color-nominal)]" />
          LIVE
        </span>
      ) : (
        <button
          onClick={resumeLive}
          className="w-24 rounded-md border border-[var(--color-border)] py-1 text-xs text-[var(--color-accent)] hover:border-[var(--color-accent-dim)]"
        >
          Resume live
        </button>
      )}
    </div>
  );
}
