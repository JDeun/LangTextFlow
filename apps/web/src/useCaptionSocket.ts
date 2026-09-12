import { useEffect, useMemo, useState } from "react";
import type { SnapshotEvent, TranscriptEvent } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export function useCaptionSocket(path = "/ws/captions") {
  const [connected, setConnected] = useState(false);
  const [segments, setSegments] = useState<Record<string, TranscriptEvent>>({});

  useEffect(() => {
    let socket: WebSocket | undefined;
    let retryTimer: number | undefined;
    let disposed = false;
    const wsUrl = API_URL.replace(/^http/, "ws") + path;

    const connect = () => {
      socket = new WebSocket(wsUrl);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!disposed) retryTimer = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => socket?.close();
      socket.onmessage = (message) => {
        const payload = JSON.parse(message.data) as TranscriptEvent | SnapshotEvent;
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

  return { connected, segments: ordered };
}
