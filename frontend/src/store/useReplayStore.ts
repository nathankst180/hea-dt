// Single source of truth for the replay dashboard. The WebSocket hook
// (hooks/useReplayWebSocket.ts) appends snapshots here as they arrive;
// every panel + the timeline scrubber reads from this store, so they're
// always showing the same instant.
//
// "Following live" vs. "scrubbing": while following live, the playhead
// auto-advances to the newest snapshot as it arrives (like watching a
// live stream). Dragging the timeline scrubber to an earlier point stops
// following live and pins the playhead there — the same behavior a video
// player has when you seek backward during a live broadcast. Dragging
// back to the latest point (or clicking "Resume live") re-enables it.

import { create } from "zustand";
import type { CycleSnapshot, SessionStatus, SpeedMultiplier, UploadResponse } from "../types";

interface ReplayState {
  session: UploadResponse | null;
  uploading: boolean;
  uploadError: string | null;

  replayStatus: SessionStatus;
  speedMultiplier: SpeedMultiplier;
  wsConnected: boolean;

  snapshots: CycleSnapshot[];
  playheadIndex: number; // index into snapshots currently being viewed
  isFollowingLive: boolean;

  // actions
  startUpload: () => void;
  uploadSucceeded: (session: UploadResponse) => void;
  uploadFailed: (message: string) => void;
  setSpeedMultiplier: (speed: SpeedMultiplier) => void;
  setReplayStatus: (status: SessionStatus) => void;
  setWsConnected: (connected: boolean) => void;
  addSnapshot: (snapshot: CycleSnapshot) => void;
  setPlayheadIndex: (index: number) => void;
  resumeLive: () => void;
  resetSession: () => void;
}

export const useReplayStore = create<ReplayState>((set, get) => ({
  session: null,
  uploading: false,
  uploadError: null,

  replayStatus: "IDLE",
  speedMultiplier: 1,
  wsConnected: false,

  snapshots: [],
  playheadIndex: -1,
  isFollowingLive: true,

  startUpload: () => set({ uploading: true, uploadError: null }),

  uploadSucceeded: (session) =>
    set({
      uploading: false,
      uploadError: null,
      session,
      snapshots: [],
      playheadIndex: -1,
      isFollowingLive: true,
      replayStatus: "IDLE",
    }),

  uploadFailed: (message) => set({ uploading: false, uploadError: message }),

  setSpeedMultiplier: (speed) => set({ speedMultiplier: speed }),

  setReplayStatus: (status) => set({ replayStatus: status }),

  setWsConnected: (connected) => set({ wsConnected: connected }),

  addSnapshot: (snapshot) => {
    const { snapshots, isFollowingLive } = get();
    const nextSnapshots = [...snapshots, snapshot];
    set({
      snapshots: nextSnapshots,
      playheadIndex: isFollowingLive ? nextSnapshots.length - 1 : get().playheadIndex,
    });
  },

  setPlayheadIndex: (index) => {
    const { snapshots } = get();
    const clamped = Math.max(0, Math.min(index, snapshots.length - 1));
    set({
      playheadIndex: clamped,
      isFollowingLive: clamped >= snapshots.length - 1,
    });
  },

  resumeLive: () => {
    const { snapshots } = get();
    set({ isFollowingLive: true, playheadIndex: snapshots.length - 1 });
  },

  resetSession: () =>
    set({
      session: null,
      uploading: false,
      uploadError: null,
      replayStatus: "IDLE",
      snapshots: [],
      playheadIndex: -1,
      isFollowingLive: true,
      wsConnected: false,
    }),
}));

export function useCurrentSnapshot(): CycleSnapshot | null {
  return useReplayStore((s) => (s.playheadIndex >= 0 ? s.snapshots[s.playheadIndex] ?? null : null));
}
