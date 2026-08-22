interface PanelFrameProps {
  title: string;
  children: React.ReactNode;
  noPadding?: boolean;
}

export function PanelFrame({ title, children, noPadding = false }: PanelFrameProps) {
  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)]">
      <div className="shrink-0 border-b border-[var(--color-border)] px-3.5 py-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--color-text-faint)]">
        {title}
      </div>
      <div className={`min-h-0 flex-1 ${noPadding ? "" : ""}`}>{children}</div>
    </div>
  );
}
