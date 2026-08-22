// Primary app navigation. "Replay Upload" is the only working mode today
// (Phase 1.5). "Live" is a clearly-marked placeholder for streaming
// straight from a running PX4/ArduPilot SITL instance — the UI slot is
// ready, the mode itself isn't built yet.

interface TopNavProps {
  sessionLabel?: string | null;
}

export function TopNav({ sessionLabel }: TopNavProps) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-panel)] px-5">
      <div className="flex items-center gap-3">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-[var(--color-accent)]/15 text-[var(--color-accent)]">
          <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
            <path
              d="M12 2v6M12 16v6M2 12h6M16 12h6M5.5 5.5l3 3M15.5 15.5l3 3M18.5 5.5l-3 3M8.5 15.5l-3 3"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
            <circle cx="12" cy="12" r="2.5" fill="currentColor" />
          </svg>
        </div>
        <div>
          <div className="text-sm font-semibold tracking-wide text-[var(--color-text)]">HEA-DT</div>
          <div className="text-[10px] uppercase tracking-widest text-[var(--color-text-faint)]">
            Health &amp; Energy-Aware Digital Twin
          </div>
        </div>
      </div>

      <nav className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-panel-raised)] p-1">
        <span className="rounded-md bg-[var(--color-accent)]/15 px-3 py-1.5 text-xs font-medium text-[var(--color-accent)]">
          Replay Upload
        </span>
        <span
          className="cursor-not-allowed rounded-md px-3 py-1.5 text-xs font-medium text-[var(--color-text-faint)]"
          title="Live telemetry streaming from a running simulator — coming later"
        >
          Live
          <span className="ml-1.5 rounded bg-[var(--color-border-strong)] px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-[var(--color-text-faint)]">
            Soon
          </span>
        </span>
      </nav>

      <div className="w-48 text-right text-xs text-[var(--color-text-faint)]">
        {sessionLabel ?? " "}
      </div>
    </header>
  );
}
