import type { DecisionKind } from "./types";

export const DECISION_STYLE: Record<DecisionKind, { color: string; label: string }> = {
  NOMINAL: { color: "var(--color-nominal)", label: "NOMINAL" },
  REOPTIMIZE: { color: "var(--color-accent)", label: "REOPTIMIZE" },
  REDUCE_SCOPE: { color: "var(--color-warn)", label: "REDUCE SCOPE" },
  ABORT: { color: "var(--color-abort)", label: "ABORT" },
};
