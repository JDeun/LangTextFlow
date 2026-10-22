import type { TranscriptEvent } from "./types";

/** Browser-local transport/paint observations; NOT audio-to-audience latency. */
export interface CaptionRenderObservation {
  segmentId: string;
  version: number;
  stage: TranscriptEvent["stage"];
  receiveToPaintMs: number;
  // Emitted-at is server wall time. Client/server clock skew makes this diagnostic only.
  serverEmitToPaintEstimateMs: number | null;
}

export function renderObservation(
  event: TranscriptEvent,
  receivedAtMonotonicMs: number,
  paintedAtMonotonicMs: number,
  paintedAtWallMs: number,
): CaptionRenderObservation {
  const emittedMs = Date.parse(event.emitted_at);
  return {
    segmentId: event.segment_id,
    version: event.version,
    stage: event.stage,
    receiveToPaintMs: Math.max(0, paintedAtMonotonicMs - receivedAtMonotonicMs),
    serverEmitToPaintEstimateMs: Number.isFinite(emittedMs)
      ? paintedAtWallMs - emittedMs : null,
  };
}

/** Only collect accepted visible versions; avoid replays and stale updates. */
export function shouldObservePaint(
  event: TranscriptEvent,
  visible: readonly TranscriptEvent[],
): boolean {
  return visible.some((item) =>
    item.segment_id === event.segment_id && item.version === event.version
  );
}
