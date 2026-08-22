// A small radial "ring" gauge for a 0-100 value. Built with plain SVG
// (stroke-dasharray trick) rather than a charting library — cheap to
// render many of, and it's the right shape for "depleting" quantities
// like RUL/SoH.

interface GaugeRingProps {
  value: number; // 0-100
  label: string;
  sublabel?: string;
  color?: string;
  size?: number;
}

export function GaugeRing({ value, label, sublabel, color = "var(--color-nominal)", size = 88 }: GaugeRingProps) {
  const clamped = Math.max(0, Math.min(100, value));
  const radius = size / 2 - 8;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - clamped / 100);

  return (
    <div className="flex flex-col items-center gap-1.5">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-border)"
            strokeWidth={6}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={6}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 0.3s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="mono-tabular text-sm font-semibold text-[var(--color-text)]">{clamped.toFixed(0)}</span>
        </div>
      </div>
      <div className="text-center">
        <div className="text-[11px] font-medium text-[var(--color-text-muted)]">{label}</div>
        {sublabel && <div className="text-[10px] text-[var(--color-text-faint)]">{sublabel}</div>}
      </div>
    </div>
  );
}
