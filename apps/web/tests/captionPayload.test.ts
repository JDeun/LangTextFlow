import assert from "node:assert/strict";
import test from "node:test";

import { parseCaptionPayload } from "../src/captionPayload.ts";

const transcript = {
  type: "transcript",
  segment_id: "seg-1",
  version: 1,
  stage: "stable",
  source_language: "ko",
  text: "안녕하세요",
  translations: {},
  start_ms: 0,
  end_ms: 1000,
  speaker: null,
  confidence: 0.9,
  correction: null,
  committed: false,
  emitted_at: "2026-09-13T00:00:00Z",
};

test("accepts a valid transcript event", () => {
  const parsed = parseCaptionPayload(JSON.stringify(transcript));
  assert.equal(parsed.type, "transcript");
  if (parsed.type === "transcript") assert.equal(parsed.segment_id, "seg-1");
});

test("accepts a valid snapshot", () => {
  const parsed = parseCaptionPayload(
    JSON.stringify({ type: "snapshot", segments: [transcript] }),
  );
  assert.equal(parsed.type, "snapshot");
  if (parsed.type === "snapshot") assert.equal(parsed.segments.length, 1);
});

test("rejects valid JSON that is not an object", () => {
  assert.throws(() => parseCaptionPayload("null"), /must be an object/);
  assert.throws(() => parseCaptionPayload("[]"), /must be an object/);
});

test("rejects malformed snapshot members", () => {
  assert.throws(
    () => parseCaptionPayload(JSON.stringify({ type: "snapshot", segments: [null] })),
    /invalid segments/,
  );
});

test("rejects invalid transcript bounds and versions", () => {
  assert.throws(
    () => parseCaptionPayload(JSON.stringify({ ...transcript, version: 0 })),
    /caption event is invalid/,
  );
  assert.throws(
    () => parseCaptionPayload(JSON.stringify({ ...transcript, end_ms: -1 })),
    /caption event is invalid/,
  );
});

test("rejects non-text websocket payloads", () => {
  assert.throws(() => parseCaptionPayload(new ArrayBuffer(4)), /must be text JSON/);
});
