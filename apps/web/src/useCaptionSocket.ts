import { useEffect, useMemo, useState } from "react";
import { getWebSocketUrl } from "./api";
import { parseCaptionPayload } from "./captionPayload";
import {
  MAX_RETRY_MS,
  isTerminalCloseCode,
  retryAfterMs,
  retryDelayMs,
  terminalMessage,
} from "./captionSocketPolicy";
import type { TranscriptEvent } from "./types";

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

        if (isTerminalCloseCode(event.code)) {
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
        const payload = parseCaptionPayload(message.data);
        if (!payload) {
          socket?.close(1003, "invalid caption payload");
          return;
        }

        if (payload.type === "snapshot") {
          setSegments(Object.fromEntries(payload.segments.map((item) => [item.segment_id, item])));
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
