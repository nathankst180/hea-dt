// PANEL 2 — Predictions & Optimization (M4-M7). Battery/motor RUL as
// depleting gauges, structural health with a real vibration spectrum
// (computed client-side from replayed accel data — see utils/dft.ts),
// current-vs-optimal speed/altitude from M6, and confidence alongside
// every prediction. M4/M5/M6/M7 are still Phase 0/2 stubs on the backend,
// so these numbers will be static ("fully healthy") until those modules
// are built — this panel is correctly wired to whatever they produce.

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useCurrentSnapshot, useReplayStore } from "../../store/useReplayStore";
import { GaugeRing } from "../ui/GaugeRing";
import { computeMagnitudeSpectrum } from "../../utils/dft";
import { formatNumber, formatPct } from "../../utils/format";

const SPECTRUM_WINDOW = 32;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] p-3.5">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-faint)]">
        {title}
      </div>
      {children}
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color = pct >= 70 ? "var(--color-nominal)" : pct >= 40 ? "var(--color-warn)" : "var(--color-danger)";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-[var(--color-border)]">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="mono-tabular text-[10px] text-[var(--color-text-faint)]">confidence {pct.toFixed(0)}%</span>
    </div>
  );
}

export function PredictionsPanel() {
  const current = useCurrentSnapshot();
  const snapshots = useReplayStore((s) => s.snapshots);
  const playheadIndex = useReplayStore((s) => s.playheadIndex);

  const spectrum = useMemo(() => {
    if (playheadIndex < 0) return [];
    const start = Math.max(0, playheadIndex - SPECTRUM_WINDOW + 1);
    const window = snapshots.slice(start, playheadIndex + 1);
    if (window.length < 4) return [];

    const samples = window.map((s) => {
      const [ax, ay, az] = s.raw_state.accel_xyz;
      return Math.sqrt(ax * ax + ay * ay + az * az);
    });
    const timestamps = window.map((s) => s.raw_state.timestamp);
    const dts = timestamps.slice(1).map((t, i) => t - timestamps[i]).filter((dt) => dt > 0);
    const avgDt = dts.length > 0 ? dts.reduce((a, b) => a + b, 0) / dts.length : 1;
    const sampleRateHz = 1 / avgDt;

    return computeMagnitudeSpectrum(samples, sampleRateHz).map((bin) => ({
      freq: bin.freqHz.toFixed(1),
      magnitude: bin.magnitude,
    }));
  }, [snapshots, playheadIndex]);

  if (!current) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-faint)]">
        Waiting for telemetry…
      </div>
    );
  }

  const { health_electrical, health_structural, energy_plan, confidence, raw_state } = current;
  const avgMotorHealth =
    health_electrical.motor_health.length > 0
      ? (health_electrical.motor_health.reduce((a, b) => a + b, 0) / health_electrical.motor_health.length) * 100
      : 100;

  const speedChartData = [
    { name: "Current", value: raw_state.groundspeed_ms ?? 0 },
    { name: "Optimal", value: energy_plan.v_opt_ms },
  ];
  const altChartData = [
    { name: "Current", value: raw_state.position_lla[2] },
    { name: "Optimal", value: energy_plan.alt_opt_m },
  ];

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3.5">
      <Section title="Electrical & Powertrain — M4">
        <div className="flex items-center justify-around gap-2">
          <GaugeRing
            value={health_electrical.battery_soh_pct}
            label="Battery SoH"
            color="var(--color-nominal)"
          />
          <GaugeRing value={avgMotorHealth} label="Motor Health" color="var(--color-accent)" />
          <div className="flex flex-col items-center gap-1 text-center">
            <span className="mono-tabular text-lg font-semibold text-[var(--color-text)]">
              {formatPct(health_electrical.p_fail_horizon * 100, 0)}
            </span>
            <span className="text-[11px] text-[var(--color-text-muted)]">P(fail within horizon)</span>
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <ConfidenceBar value={health_electrical.confidence} />
        </div>
      </Section>

      <Section title="Structural Health — M5">
        <div className="flex items-center gap-4">
          <GaugeRing
            value={(1 - health_structural.structural_anomaly_score) * 100}
            label="Structural"
            sublabel={health_structural.imbalance_flag ? "imbalance flagged" : "nominal"}
            color={health_structural.imbalance_flag ? "var(--color-danger)" : "var(--color-nominal)"}
            size={76}
          />
          <div className="h-20 flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={spectrum} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
                <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="freq" tick={{ fontSize: 9, fill: "var(--color-text-faint)" }} interval={3} />
                <YAxis tick={{ fontSize: 9, fill: "var(--color-text-faint)" }} width={24} />
                <Tooltip
                  contentStyle={{ background: "var(--color-panel-raised)", border: "1px solid var(--color-border)", fontSize: 11 }}
                  labelFormatter={(v) => `${v} Hz`}
                />
                <Bar dataKey="magnitude" fill="var(--color-accent)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="mt-1 flex items-center justify-between">
          <span className="text-[10px] text-[var(--color-text-faint)]">
            live vibration spectrum (accel magnitude, {SPECTRUM_WINDOW}-sample window)
          </span>
          <ConfidenceBar value={health_structural.confidence} />
        </div>
      </Section>

      <Section title="Energy Optimization — M6">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="mb-1 text-center text-[10px] text-[var(--color-text-faint)]">Speed (m/s)</div>
            <div className="h-24">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={speedChartData} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--color-text-faint)" }} />
                  <YAxis tick={{ fontSize: 9, fill: "var(--color-text-faint)" }} width={24} />
                  <Bar dataKey="value" fill="var(--color-nominal)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <div>
            <div className="mb-1 text-center text-[10px] text-[var(--color-text-faint)]">Altitude (m)</div>
            <div className="h-24">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={altChartData} margin={{ top: 4, right: 4, left: -24, bottom: 0 }}>
                  <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--color-text-faint)" }} />
                  <YAxis tick={{ fontSize: 9, fill: "var(--color-text-faint)" }} width={24} />
                  <Bar dataKey="value" fill="var(--color-accent)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-[11px] text-[var(--color-text-muted)]">
          <span>
            energy margin:{" "}
            <span className="mono-tabular text-[var(--color-text)]">{formatNumber(energy_plan.energy_margin_wh, 0)} Wh</span>
          </span>
          <ConfidenceBar value={confidence.per_prediction["energy_margin"] ?? 1} />
        </div>
      </Section>

      <Section title="Fused Confidence — M7">
        <div className="flex items-center justify-between">
          <span className="text-xs text-[var(--color-text-muted)]">decision_confidence</span>
          <ConfidenceBar value={confidence.decision_confidence} />
        </div>
      </Section>
    </div>
  );
}
