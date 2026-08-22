import { useReplayWebSocket } from "../hooks/useReplayWebSocket";
import { useReplayStatusPolling } from "../hooks/useReplayStatusPolling";
import { useReplayStore } from "../store/useReplayStore";
import { PanelFrame } from "./PanelFrame";
import { TimelineScrubber } from "./TimelineScrubber";
import { DigitalTwinPanel } from "./panels/DigitalTwinPanel";
import { PredictionsPanel } from "./panels/PredictionsPanel";
import { ClosedLoopPanel } from "./panels/ClosedLoopPanel";
import { DecisionLogPanel } from "./panels/DecisionLogPanel";

export function Dashboard() {
  const sessionId = useReplayStore((s) => s.session?.session_id ?? null);
  useReplayWebSocket(sessionId);
  useReplayStatusPolling(sessionId);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="grid min-h-0 flex-1 grid-cols-[1.3fr_1fr] grid-rows-[1.35fr_1fr] gap-3 overflow-hidden p-3">
        <PanelFrame title="Live Digital Twin — M1-M3">
          <DigitalTwinPanel />
        </PanelFrame>
        <PanelFrame title="Predictions &amp; Optimization — M4-M7">
          <PredictionsPanel />
        </PanelFrame>
        <PanelFrame title="Closed Loop — M8-M11">
          <ClosedLoopPanel />
        </PanelFrame>
        <PanelFrame title="Decision Log — M12">
          <DecisionLogPanel />
        </PanelFrame>
      </div>
      <TimelineScrubber />
    </div>
  );
}
