// The app's entry screen: drag-and-drop a telemetry file, see validation
// feedback (row count / duration, or a clear error), pick a replay speed,
// then hand off to the Dashboard.

import { useCallback, useRef, useState } from "react";
import { startReplay, uploadTelemetry } from "../api/client";
import { useReplayStore } from "../store/useReplayStore";
import { SPEED_OPTIONS, type SpeedMultiplier } from "../types";
import { formatDuration } from "../utils/format";

interface UploadScreenProps {
  onReplayStarted: () => void;
}

export function UploadScreen({ onReplayStarted }: UploadScreenProps) {
  const session = useReplayStore((s) => s.session);
  const uploading = useReplayStore((s) => s.uploading);
  const uploadError = useReplayStore((s) => s.uploadError);
  const speedMultiplier = useReplayStore((s) => s.speedMultiplier);
  const startUpload = useReplayStore((s) => s.startUpload);
  const uploadSucceeded = useReplayStore((s) => s.uploadSucceeded);
  const uploadFailed = useReplayStore((s) => s.uploadFailed);
  const setSpeedMultiplier = useReplayStore((s) => s.setSpeedMultiplier);
  const resetSession = useReplayStore((s) => s.resetSession);

  const [isDragOver, setIsDragOver] = useState(false);
  const [starting, setStarting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleFile = useCallback(
    async (file: File) => {
      const ext = file.name.split(".").pop()?.toLowerCase();
      if (ext !== "csv" && ext !== "parquet") {
        uploadFailed(`Unsupported file type ".${ext}" — expected .csv or .parquet`);
        return;
      }
      startUpload();
      try {
        const response = await uploadTelemetry(file);
        uploadSucceeded(response);
      } catch (err) {
        uploadFailed(err instanceof Error ? err.message : "upload failed");
      }
    },
    [startUpload, uploadSucceeded, uploadFailed],
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragOver(false);
      const file = event.dataTransfer.files?.[0];
      if (file) void handleFile(file);
    },
    [handleFile],
  );

  const onStartReplay = useCallback(async () => {
    if (!session) return;
    setStarting(true);
    try {
      await startReplay(session.session_id, speedMultiplier);
      onReplayStarted();
    } catch (err) {
      uploadFailed(err instanceof Error ? err.message : "failed to start replay");
    } finally {
      setStarting(false);
    }
  }, [session, speedMultiplier, onReplayStarted, uploadFailed]);

  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="w-full max-w-xl">
        <div className="mb-8 text-center">
          <h1 className="text-xl font-semibold text-[var(--color-text)]">Replay a recorded flight</h1>
          <p className="mt-1.5 text-sm text-[var(--color-text-muted)]">
            Upload telemetry from a past flight. It runs through the real HEA-DT pipeline —
            validation, twin-state estimation, prediction, and decision — exactly as live data would.
          </p>
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-14 text-center transition-colors ${
            isDragOver
              ? "border-[var(--color-accent)] bg-[var(--color-accent)]/5"
              : "border-[var(--color-border-strong)] bg-[var(--color-panel)] hover:border-[var(--color-accent-dim)]"
          }`}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.parquet"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void handleFile(file);
              e.target.value = "";
            }}
          />
          <svg viewBox="0 0 24 24" fill="none" className="mb-3 h-9 w-9 text-[var(--color-text-faint)]">
            <path
              d="M12 16V4m0 0L7 9m5-5l5 5M5 20h14"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {uploading ? (
            <p className="text-sm text-[var(--color-text-muted)]">Uploading &amp; validating…</p>
          ) : (
            <>
              <p className="text-sm text-[var(--color-text)]">
                Drag a telemetry file here, or <span className="text-[var(--color-accent)]">browse</span>
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-faint)]">.csv or .parquet</p>
            </>
          )}
        </div>

        {uploadError && (
          <div className="mt-4 rounded-lg border border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 px-4 py-3 text-sm text-[var(--color-danger)]">
            {uploadError}
          </div>
        )}

        {session && !uploading && (
          <div className="mt-5 rounded-lg border border-[var(--color-border)] bg-[var(--color-panel)] p-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-nominal)]">
                  <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                    <path
                      d="M20 6L9 17l-5-5"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  {session.filename}
                </div>
                <div className="mono-tabular mt-1 text-xs text-[var(--color-text-muted)]">
                  {session.num_rows.toLocaleString()} rows &middot; {formatDuration(session.duration_s)} of recorded flight
                </div>
              </div>
              <button
                onClick={resetSession}
                className="text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"
              >
                Remove
              </button>
            </div>

            <div className="mt-4">
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--color-text-faint)]">
                Replay speed
              </div>
              <div className="flex gap-2">
                {SPEED_OPTIONS.map((speed) => (
                  <button
                    key={speed}
                    onClick={() => setSpeedMultiplier(speed as SpeedMultiplier)}
                    className={`flex-1 rounded-md border px-3 py-2 text-sm font-medium transition-colors ${
                      speedMultiplier === speed
                        ? "border-[var(--color-accent)] bg-[var(--color-accent)]/15 text-[var(--color-accent)]"
                        : "border-[var(--color-border)] bg-[var(--color-panel-raised)] text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)]"
                    }`}
                  >
                    {speed}x
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={onStartReplay}
              disabled={starting}
              className="mt-5 w-full rounded-md bg-[var(--color-accent)] py-2.5 text-sm font-semibold text-[#03141a] transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {starting ? "Starting…" : "Start Replay"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
