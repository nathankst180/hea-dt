// Opens the /ws/state connection for a session and feeds every incoming
// CycleSnapshot into the zustand store. Connects as soon as a session_id
// is known (even before replay/start is called) so we never race the
// backend's backfill — see api/replay_session.py's subscribe(), which
// sends everything already produced the moment a client connects.

import { useEffect, useRef } from "react";
import { stateWebSocketUrl } from "../api/client";
import { useReplayStore } from "../store/useReplayStore";
import type { CycleSnapshot } from "../types";

export function useReplayWebSocket(sessionId: string | null): void {
  const addSnapshot = useReplayStore((s) => s.addSnapshot);
  const setWsConnected = useReplayStore((s) => s.setWsConnected);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    const socket = new WebSocket(stateWebSocketUrl(sessionId));
    socketRef.current = socket;

    socket.onopen = () => setWsConnected(true);
    socket.onclose = () => setWsConnected(false);
    socket.onerror = () => setWsConnected(false);
    socket.onmessage = (event: MessageEvent<string>) => {
      try {
        const snapshot = JSON.parse(event.data) as CycleSnapshot;
        addSnapshot(snapshot);
      } catch (err) {
        console.error("failed to parse WS snapshot", err);
      }
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [sessionId, addSnapshot, setWsConnected]);
}
