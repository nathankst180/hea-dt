// PANEL 3 — Closed Loop (M8-M11), the core novelty view: prediction ->
// decision -> command -> ack, animated as a continuous flow so a viewer
// can visually follow the loop actually closing each cycle.

import { useCurrentSnapshot } from "../../store/useReplayStore";
import { DECISION_STYLE } from "../../constants";
import { formatNumber } from "../../utils/format";

function FlowNode({ title, value, active }: { title: string; value: string; active: boolean }) {
  return (
    <div
      className="flex min-w-[110px] flex-col items-center gap-1 rounded-lg border px-3 py-2.5 text-center"
      style={{
        borderColor: active ? "var(--color-accent)" : "var(--color-border)",
        backgroundColor: active ? "rgba(56,217,255,0.06)" : "var(--color-panel)",
      }}
    >
      <span className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{title}</span>
      <span className="mono-tabular text-xs font-medium text-[var(--color-text)]">{value}</span>
    </div>
  );
}

function FlowConnector() {
  return (
    <div className="flex flex-1 items-center px-1">
      <svg width="100%" height="10" preserveAspectRatio="none" className="overflow-visible">
        <line
          x1="0"
          y1="5"
          x2="100%"
          y2="5"
          stroke="var(--color-accent)"
          strokeWidth="2"
          strokeDasharray="6 6"
          className="flow-line"
        />
      </svg>
    </div>
  );
}

export function ClosedLoopPanel() {
  const current = useCurrentSnapshot();

  if (!current) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-faint)]">
        Waiting for telemetry…
      </div>
    );
  }

  const { health_electrical, feasibility, decision, command, ack } = current;
  const style = DECISION_STYLE[decision.kind];

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
      <style>{`
        @keyframes flowDash { to { stroke-dashoffset: -24; } }
        .flow-line { animation: flowDash 1s linear infinite; }
      `}</style>

      <div className="flex items-center">
        <FlowNode title="Prediction" value={`P(fail) ${(health_electrical.p_fail_horizon * 100).toFixed(0)}%`} active />
        <FlowConnector />
        <FlowNode title="Feasibility" value={feasibility.feasible ? "feasible" : "infeasible"} active />
        <FlowConnector />
        <FlowNode title="Decision" value={style.label} active />
        <FlowConnector />
        <FlowNode title="Command" value={command ? command.kind : "none"} active={!!command} />
        <FlowConnector />
        <FlowNode title="Ack" value={ack ? (ack.accepted ? "accepted" : "rejected") : "—"} active={!!ack} />
      </div>

      <div
        className="rounded-xl border-2 p-5"
        style={{ borderColor: style.color, backgroundColor: `${style.color}0d` }}
      >
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[11px] uppercase tracking-widest text-[var(--color-text-faint)]">
              Adaptive Mission Decision — M9
            </div>
            <div className="mt-1 text-2xl font-bold tracking-tight" style={{ color: style.color }}>
              {style.label}
            </div>
          </div>
          <div
            className="h-4 w-4 rounded-full"
            style={{ backgroundColor: style.color, boxShadow: `0 0 12px 2px ${style.color}` }}
          />
        </div>
        <p className="mt-3 text-sm leading-relaxed text-[var(--color-text-muted)]">{decision.triggered_by}</p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] p-3.5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-faint)]">
            Command — M10 sanctioned
          </div>
          {command ? (
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">kind</span>
                <span className="mono-tabular text-[var(--color-text)]">{command.kind}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">reversible</span>
                <span className="mono-tabular text-[var(--color-text)]">{command.reversible ? "yes" : "no"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">requires human</span>
                <span className="mono-tabular text-[var(--color-text)]">{command.requires_human ? "yes" : "no"}</span>
              </div>
              <div className="mt-1.5 rounded bg-[var(--color-panel-raised)] p-2 text-[10px] text-[var(--color-text-faint)]">
                {JSON.stringify(command.payload)}
              </div>
            </div>
          ) : (
            <div className="text-xs text-[var(--color-text-faint)]">no command sanctioned this cycle</div>
          )}
        </div>

        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] p-3.5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-faint)]">
            Ack — M11 write-back
          </div>
          {ack ? (
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">accepted</span>
                <span
                  className="mono-tabular font-medium"
                  style={{ color: ack.accepted ? "var(--color-nominal)" : "var(--color-danger)" }}
                >
                  {ack.accepted ? "true" : "false"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[var(--color-text-muted)]">latency</span>
                <span className="mono-tabular text-[var(--color-text)]">{formatNumber(ack.latency_ms, 1)} ms</span>
              </div>
              <div className="mt-1.5 rounded bg-[var(--color-panel-raised)] p-2 text-[10px] text-[var(--color-text-faint)]">
                {ack.detail}
              </div>
            </div>
          ) : (
            <div className="text-xs text-[var(--color-text-faint)]">no ack this cycle</div>
          )}
        </div>
      </div>
    </div>
  );
}
