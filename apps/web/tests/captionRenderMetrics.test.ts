import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { CaptionRenderStats, renderObservation, shouldObservePaint } from "../src/captionRenderMetrics";
import type { TranscriptEvent } from "../src/types";

const event = {
  type: "transcript", segment_id: "s1", version: 2, stage: "translated",
  source_language: "en", text: "Hello", translations: { ko: "안녕" },
  start_ms: 0, end_ms: 1000, speaker: null, confidence: null,
  correction: null, committed: false, emitted_at: "2026-10-22T00:00:00.000Z",
} as TranscriptEvent;

describe("caption render measurement primitives", () => {
  it("uses monotonic browser times for receive-to-paint", () => {
    const observation = renderObservation(event, 123, 150, Date.parse(event.emitted_at) + 100);
    assert.equal(observation.receiveToPaintMs, 27);
    assert.equal(observation.serverEmitToPaintEstimateMs, 100);
  });
  it("clamps monotonic clock inversions and labels clock-skew-sensitive estimate", () => {
    const observation = renderObservation(
      { ...event, emitted_at: "invalid" }, 150, 100, 0,
    );
    assert.equal(observation.receiveToPaintMs, 0);
    assert.equal(observation.serverEmitToPaintEstimateMs, null);
  });
  it("does not count superseded or invisible versions", () => {
    assert.equal(shouldObservePaint(event, [{ ...event, version: 3 }]), false);
    assert.equal(shouldObservePaint(event, [event]), true);
  });
});

describe("bounded caption render quantiles", () => {
  it("computes stage-specific p50/p95/p99 without retaining unbounded samples", () => {
    const stats = new CaptionRenderStats(3);
    for (const ms of [1, 2, 3, 4]) {
      stats.add({ ...renderObservation(event, 0, ms, 0), receiveToPaintMs: ms });
    }
    const summary = stats.summary().translated;
    assert.equal(summary.count, 3);
    assert.equal(summary.p50Ms, 3);
    assert.equal(summary.p95Ms, 3.9);
    assert.equal(summary.p99Ms, 3.98);
    stats.reset();
    assert.deepEqual(stats.summary(), {});
  });
  it("rejects invalid sample limits and ignores non-finite observations", () => {
    assert.throws(() => new CaptionRenderStats(0), RangeError);
    const stats = new CaptionRenderStats();
    stats.add({ ...renderObservation(event, 0, 1, 0), receiveToPaintMs: NaN });
    assert.deepEqual(stats.summary(), {});
  });
});
