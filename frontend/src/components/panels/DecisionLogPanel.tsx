// PANEL 4 — Decision Log (M12): every decision logged with its rationale,
// most recent first. Clicking an entry scrubs the whole dashboard (all 4
// panels + the timeline) back to that moment, via the shared playhead in
// the store — no separate "log view time" to keep in sync.

import { useReplayStore } from "../../store/useReplayStore";
import { DECISION_STYLE } from "../../constants";

export function DecisionLogPanel() {
  const snapshots = useReplayStore((s) => s.snapshots);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);
  const setPlayheadIndex = useReplayStore((s) => s.setPlayheadIndex);

  const entries = [...snapshots].reverse();

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 divide-y divide-[var(--color-border)] overflow-y-auto">
        {entries.length === 0 && (
          <div className="p-4 text-center text-xs text-[var(--color-text-faint)]">No decisions logged yet.</div>
        )}
        {entries.map((snap) => {
          const isActive = snap.cycle - 1 === playheadIndex;
          const style = DECISION_STYLE[snap.decision.kind];
          return (
            <button
              key={snap.cycle}
              onClick={() => setPlayheadIndex(snap.cycle - 1)}
              className={`w-full px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--color-panel-raised)] ${
                isActive ? "bg-[var(--color-accent)]/10" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <span
                  className="rounded px-1.5 py-0.5 text-[10px] font-semibold"
                  style={{ color: style.color, backgroundColor: `${style.color}1a` }}
                >
                  {style.label}
                </span>
                <span className="mono-tabular text-[10px] text-[var(--color-text-faint)]">
                  cycle {snap.cycle} &middot; t={snap.log_record.timestamp.toFixed(1)}
                </span>
              </div>
              <p className="mt-1.5 text-xs leading-snug text-[var(--color-text-muted)]">{snap.log_record.rationale}</p>
              <div className="mono-tabular mt-1 truncate text-[9px] text-[var(--color-text-faint)]">
                hash {snap.log_record.record_hash.slice(0, 16)}…
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
