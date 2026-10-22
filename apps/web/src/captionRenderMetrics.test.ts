import { describe, expect, it } from "vitest";
import { renderObservation, shouldObservePaint } from "./captionRenderMetrics";
import type { TranscriptEvent } from "./types";

const event = {
  type: "transcript", segment_id: "s1", version: 2, stage: "translated",
  source_language: "en", text: "Hello", translations: { ko: "안녕" },
  start_ms: 0, end_ms: 1000, speaker: null, confidence: null,
  correction: null, committed: false, emitted_at: "2026-10-22T00:00:00.000Z",
} as TranscriptEvent;

describe("caption render measurement primitives", () => {
  it("uses monotonic browser times for receive-to-paint", () => {
    const observation = renderObservation(event, 123, 150, Date.parse(event.emitted_at) + 100);
    expect(observation.receiveToPaintMs).toBe(27);
    expect(observation.serverEmitToPaintEstimateMs).toBe(100);
  });
  it("clamps monotonic clock inversions and labels clock-skew-sensitive estimate", () => {
    const observation = renderObservation(
      { ...event, emitted_at: "invalid" }, 150, 100, 0,
    );
    expect(observation.receiveToPaintMs).toBe(0);
    expect(observation.serverEmitToPaintEstimateMs).toBeNull();
  });
  it("does not count superseded or invisible versions", () => {
    expect(shouldObservePaint(event, [{ ...event, version: 3 }])).toBe(false);
    expect(shouldObservePaint(event, [event])).toBe(true);
  });
});
