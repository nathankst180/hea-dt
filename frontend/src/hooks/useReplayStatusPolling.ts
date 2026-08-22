// The WebSocket only carries per-cycle snapshots, not session lifecycle
// (RUNNING -> FINISHED/STOPPED/ERROR) — that comes from polling
// GET /replay/status. Polls at a modest interval for as long as a
// session exists; cheap enough at dashboard scale.

import { useEffect } from "react";
import { fetchReplayStatus } from "../api/client";
import { useReplayStore } from "../store/useReplayStore";

const POLL_INTERVAL_MS = 1000;

export function useReplayStatusPolling(sessionId: string | null): void {
  const setReplayStatus = useReplayStore((s) => s.setReplayStatus);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchReplayStatus(sessionId);
        if (!cancelled) setReplayStatus(status.status);
      } catch {
        // transient network hiccup; next tick will retry
      }
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [sessionId, setReplayStatus]);
}
