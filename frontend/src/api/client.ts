// Thin wrapper around the HEA-DT backend API (api/server.py). Base URL is
// configurable via VITE_API_BASE_URL (see .env.example); defaults to the
// backend's default uvicorn port for local development.

import type { LogsResponse, ReplayStatusResponse, UploadResponse } from "../types";

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://127.0.0.1:8000";

export const WS_BASE_URL: string = API_BASE_URL.replace(/^http/, "ws");

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // response body wasn't JSON; fall back to statusText
    }
    throw new Error(detail || `request failed with status ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function uploadTelemetry(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: "POST",
    body: formData,
  });
  return parseJsonOrThrow<UploadResponse>(response);
}

export async function startReplay(sessionId: string, speedMultiplier: number): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/replay/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, speed_multiplier: speedMultiplier }),
  });
  await parseJsonOrThrow(response);
}

export async function stopReplay(sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/replay/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  await parseJsonOrThrow(response);
}

export async function fetchReplayStatus(sessionId: string): Promise<ReplayStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/replay/status?session_id=${encodeURIComponent(sessionId)}`);
  return parseJsonOrThrow<ReplayStatusResponse>(response);
}

export async function fetchLogs(sessionId: string): Promise<LogsResponse> {
  const response = await fetch(`${API_BASE_URL}/logs?session_id=${encodeURIComponent(sessionId)}`);
  return parseJsonOrThrow<LogsResponse>(response);
}

export function stateWebSocketUrl(sessionId: string): string {
  return `${WS_BASE_URL}/ws/state?session_id=${encodeURIComponent(sessionId)}`;
}
