import assert from "node:assert/strict";
import test from "node:test";

import { parseCaptionPayload } from "../src/captionPayload.ts";

const validTranscript = {
  type: "transcript",
  segment_id: "segment-1",
  version: 1,
  stage: "stable",
  source_language: "ko",
  text: "안녕하세요",
  translations: { en: "Hello" },
  start_ms: 0,
  end_ms: 500,
  speaker: null,
  confidence: 0.9,
  correction: null,
  committed: false,
  emitted_at: "2026-09-13T03:00:00Z",
};

test("rejects syntactically invalid or non-object JSON", () => {
  assert.equal(parseCaptionPayload("{not-json"), null);
  assert.equal(parseCaptionPayload("null"), null);
  assert.equal(parseCaptionPayload("[]"), null);
  assert.equal(parseCaptionPayload("{}"), null);
  assert.equal(parseCaptionPayload(new ArrayBuffer(8)), null);
});

test("rejects snapshots containing structurally invalid segments", () => {
  assert.equal(
    parseCaptionPayload(JSON.stringify({ type: "snapshot", segments: [null] })),
    null,
  );
  assert.equal(
    parseCaptionPayload(
      JSON.stringify({
        type: "snapshot",
        segments: [{ ...validTranscript, version: "1" }],
      }),
    ),
    null,
  );
});

test("rejects invalid transcript numeric and collection fields", () => {
  for (const payload of [
    { ...validTranscript, version: -1 },
    { ...validTranscript, version: 1.5 },
    { ...validTranscript, start_ms: -1 },
    { ...validTranscript, end_ms: -1 },
    { ...validTranscript, end_ms: 100, start_ms: 200 },
    { ...validTranscript, translations: { en: 42 } },
    { ...validTranscript, confidence: 2 },
    { ...validTranscript, stage: "unknown" },
  ]) {
    assert.equal(parseCaptionPayload(JSON.stringify(payload)), null);
  }
});

test("accepts valid transcript and snapshot payloads", () => {
  const transcript = parseCaptionPayload(JSON.stringify(validTranscript));
  assert.ok(transcript);
  assert.equal(transcript.type, "transcript");

  const snapshot = parseCaptionPayload(
    JSON.stringify({ type: "snapshot", segments: [validTranscript] }),
  );
  assert.ok(snapshot);
  assert.equal(snapshot.type, "snapshot");
  assert.equal(snapshot.segments.length, 1);
});
