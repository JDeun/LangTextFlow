import { useEffect, useMemo, useState } from "react";
import { getWebSocketUrl } from "./api";
import type { SnapshotEvent, TranscriptEvent } from "./types";

const BASE_RETRY_MS = 750;
const MAX_RETRY_MS = 15_000;
const RETRY_JITTER_RATIO = 0.2;
const TERMINAL_CLOSE_CODES = new Set([4403, 4404]);

function retryAfterMs(reason: string) {
  const match = reason.match(/retry after\s+(\d+)s/i);
  if (!match) return null;
  return Math.max(1, Number(match[1])) * 1000;
}

function retryDelayMs(attempt: number) {
  const exponential = Math.min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** Math.max(0, attempt));
  const jitter = exponential * RETRY_JITTER_RATIO * (Math.random() * 2 - 1);
  return Math.max(BASE_RETRY_MS, Math.round(exponential + jitter));
}

function terminalMessage(code: number, reason: string) {
  if (code === 4403) return reason || "이 WebSocket 연결은 허용되지 않습니다.";
  if (code === 4404) return reason || "세션을 찾을 수 없거나 종료되었습니다.";
  return "";
}

export function useCaptionSocket(path = "/ws/captions") {
  const [connected, setConnected] = useState(false);
  const [segments, setSegments] = useState<Record<string, TranscriptEvent>>({});
  const [terminalError, setTerminalError] = useState("");

  useEffect(() => {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let disposed = false;
    let retryAttempt = 0;
    const wsUrl = getWebSocketUrl(path);

    const scheduleReconnect = (delayMs?: number) => {
      if (disposed) return;
      if (retryTimer) window.clearTimeout(retryTimer);
      const delay = delayMs ?? retryDelayMs(retryAttempt++);
      retryTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (disposed) return;
      setTerminalError("");
      socket = new WebSocket(wsUrl);
      socket.onopen = () => {
        retryAttempt = 0;
        setConnected(true);
      };
      socket.onclose = (event) => {
        setConnected(false);
        if (disposed) return;

        if (TERMINAL_CLOSE_CODES.has(event.code)) {
          setTerminalError(terminalMessage(event.code, event.reason));
          return;
        }
        if (event.code === 4429) {
          scheduleReconnect(retryAfterMs(event.reason) ?? MAX_RETRY_MS);
          return;
        }
        scheduleReconnect();
      };
      socket.onerror = () => socket?.close();
      socket.onmessage = (message) => {
        let payload: TranscriptEvent | SnapshotEvent;
        try {
          payload = JSON.parse(message.data) as TranscriptEvent | SnapshotEvent;
        } catch {
          socket?.close(1003, "invalid caption payload");
          return;
        }

        if (payload.type === "snapshot") {
          if (!Array.isArray(payload.segments)) {
            socket?.close(1003, "invalid caption snapshot");
            return;
          }
          setSegments(Object.fromEntries(payload.segments.map((item) => [item.segment_id, item])));
          return;
        }
        if (!payload.segment_id || typeof payload.version !== "number") {
          socket?.close(1003, "invalid caption event");
          return;
        }
        setSegments((current) => {
          const previous = current[payload.segment_id];
          if (previous && previous.version >= payload.version) return current;
          return { ...current, [payload.segment_id]: payload };
        });
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [path]);

  const ordered = useMemo(
    () => Object.values(segments).sort((a, b) => a.start_ms - b.start_ms),
    [segments],
  );

  return { connected, segments: ordered, terminalError };
}
