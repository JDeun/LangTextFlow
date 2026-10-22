import { useEffect, useMemo, useRef, useState } from "react";
import { getWebSocketUrl } from "./api";
import { parseCaptionPayload } from "./captionPayload";
import {
  CLIENT_PROTOCOL_ERROR_CODE,
  MAX_RETRY_MS,
  isTerminalCloseCode,
  retryAfterMs,
  retryDelayMs,
  terminalMessage,
} from "./captionSocketPolicy";
import {
  applyScopedCaption, createScopedCaptions, clearDisconnectedCaptionScope,
  visibleScopedCaptions,
} from "./scopedCaptions";
import type { TranscriptEvent } from "./types";
import { renderObservation, shouldObservePaint } from "./captionRenderMetrics";

export function useCaptionSocket(path = "/ws/captions") {
  // Pair transcript data with the source route. On an immediate route change,
  // render no old caption even before the effect cleans up the old connection.
  const [scoped, setScoped] = useState(() => createScopedCaptions(path));
  const [connection, setConnection] = useState({ path, connected: false });
  const [terminal, setTerminal] = useState({ path, message: "" });
  const pendingPaint = useRef(new Map<string, { event: TranscriptEvent; receivedAt: number }>());

  useEffect(() => {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let disposed = false;
    let retryAttempt = 0;
    const wsUrl = getWebSocketUrl(path);

    pendingPaint.current.clear();
    setScoped(createScopedCaptions(path));
    setConnection({ path, connected: false });
    setTerminal({ path, message: "" });

    const scheduleReconnect = (delayMs?: number) => {
      if (disposed) return;
      if (retryTimer) window.clearTimeout(retryTimer);
      const delay = delayMs ?? retryDelayMs(retryAttempt++);
      retryTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (disposed) return;
      setTerminal({ path, message: "" });
      const activeSocket = new WebSocket(wsUrl);
      socket = activeSocket;
      activeSocket.onopen = () => {
        if (disposed || socket !== activeSocket) return;
        retryAttempt = 0;
        setConnection({ path, connected: true });
      };
      activeSocket.onclose = (event) => {
        if (disposed || socket !== activeSocket) return;
        setConnection({ path, connected: false });
        // A reconnect is not evidence the old transcript is still current.
        // Clear the route's visible projection until the server sends a fresh
        // authorized snapshot; do not erase captions from a newer route.
        pendingPaint.current.clear();
        setScoped((current) => clearDisconnectedCaptionScope(current, path));
        if (isTerminalCloseCode(event.code)) {
          // Terminal authorization failures also remove prior captions.
          setTerminal({ path, message: terminalMessage(event.code, event.reason) });
          return;
        }
        if (event.code === 4429) {
          scheduleReconnect(retryAfterMs(event.reason) ?? MAX_RETRY_MS);
          return;
        }
        scheduleReconnect();
      };
      activeSocket.onerror = () => {
        if (!disposed && socket === activeSocket) activeSocket.close();
      };
      activeSocket.onmessage = (message) => {
        if (disposed || socket !== activeSocket) return;
        let payload;
        try {
          payload = parseCaptionPayload(message.data);
        } catch {
          activeSocket.close(CLIENT_PROTOCOL_ERROR_CODE, "invalid caption payload");
          return;
        }
        if (payload.type === "transcript") {
          const key = `${payload.segment_id}:${payload.version}`;
          pendingPaint.current.set(key, { event: payload, receivedAt: performance.now() });
        }
        setScoped((current) => applyScopedCaption(current, path, payload));
      };
    };

    connect();
    return () => {
      disposed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [path]);

  const segments: TranscriptEvent[] = useMemo(
    () => visibleScopedCaptions(scoped, path),
    [scoped, path],
  );
  useEffect(() => {
    if (!connection.connected || connection.path !== path) return;
    // rAF executes before paint; the second rAF is a best-effort post-paint
    // observation, NOT an exact compositor/presentation timestamp.
    const first = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        const now = performance.now();
        const wall = Date.now();
        for (const [key, pending] of pendingPaint.current) {
          if (shouldObservePaint(pending.event, segments)) {
            const observation = renderObservation(pending.event, pending.receivedAt, now, wall);
            window.dispatchEvent(new CustomEvent("langtextflow:caption-render-metric", {
              detail: observation,
            }));
          }
          // Drop superseded/filtered versions too: never replay stale samples.
          pendingPaint.current.delete(key);
        }
      });
    });
    return () => window.cancelAnimationFrame(first);
  }, [segments, connection.connected, connection.path, path]);

  return {
    connected: connection.path === path && connection.connected,
    segments,
    terminalError: terminal.path === path ? terminal.message : "",
  };
}
