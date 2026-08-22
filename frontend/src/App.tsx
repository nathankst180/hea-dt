import { useState } from "react";
import { TopNav } from "./components/TopNav";
import { UploadScreen } from "./components/UploadScreen";
import { Dashboard } from "./components/Dashboard";
import { useReplayStore } from "./store/useReplayStore";

type View = "upload" | "dashboard";

export default function App() {
  const [view, setView] = useState<View>("upload");
  const session = useReplayStore((s) => s.session);
  const resetSession = useReplayStore((s) => s.resetSession);

  const handleNewUpload = () => {
    resetSession();
    setView("upload");
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[var(--color-bg)]">
      <TopNav sessionLabel={session ? `${session.filename} · session ${session.session_id.slice(0, 8)}` : null} />
      {view === "dashboard" && (
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-panel)] px-5 py-2">
          <span className="text-xs text-[var(--color-text-faint)]">Replaying uploaded telemetry through the live pipeline</span>
          <button onClick={handleNewUpload} className="text-xs text-[var(--color-accent)] hover:underline">
            Upload a different flight
          </button>
        </div>
      )}
      {view === "upload" ? <UploadScreen onReplayStarted={() => setView("dashboard")} /> : <Dashboard />}
    </div>
  );
}
