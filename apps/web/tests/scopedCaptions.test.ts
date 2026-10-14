import assert from "node:assert/strict";
import test from "node:test";
import {
  applyScopedCaption, createScopedCaptions, visibleScopedCaptions,
} from "../src/scopedCaptions.ts";
import type { TranscriptEvent } from "../src/types.ts";

function caption(segment: string, version: number): TranscriptEvent {
  return {
    type: "transcript", segment_id: segment, version,
    stage: "stable", source_language: "ko", text: segment,
    translations: {}, start_ms: 0, end_ms: 500,
    speaker: null, confidence: null, correction: null, committed: false,
    emitted_at: "2026-10-14T00:00:00Z",
  };
}

test("route changes never display the previous audience join code's captions", () => {
  const oldRoute = "/ws/audience/OLD123";
  const newRoute = "/ws/audience/NEW456";
  const former = applyScopedCaption(createScopedCaptions(oldRoute), oldRoute, caption("old", 1));
  assert.equal(visibleScopedCaptions(former, oldRoute)[0].text, "old");
  assert.deepEqual(visibleScopedCaptions(former, newRoute), []);
  const next = createScopedCaptions(newRoute);
  assert.equal(applyScopedCaption(next, oldRoute, caption("stale", 2)), next);
  assert.deepEqual(visibleScopedCaptions(next, newRoute), []);
});

test("snapshot replaces old data and events may only advance monotonic revisions", () => {
  const path = "/ws/captions";
  let state = applyScopedCaption(createScopedCaptions(path), path, caption("same", 2));
  state = applyScopedCaption(state, path, caption("same", 1));
  assert.equal(visibleScopedCaptions(state, path)[0].version, 2);
  state = applyScopedCaption(state, path, { type: "snapshot", segments: [caption("fresh", 1)] });
  assert.deepEqual(visibleScopedCaptions(state, path).map(x => x.segment_id), ["fresh"]);
});

test("disposed old-path snapshot cannot overwrite a new session", () => {
  const next = createScopedCaptions("/ws/audience/NEW");
  const other = applyScopedCaption(next, "/ws/audience/OLD", {
    type: "snapshot", segments: [caption("secret", 1)],
  });
  assert.equal(other, next);
});
