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

test("retains optional original ASR text and rejects malformed evidence", () => {
  const parsed = parseCaptionPayload(JSON.stringify({ ...transcript, original_text: "raw ASR" }));
  if (parsed.type === "transcript") assert.equal(parsed.original_text, "raw ASR");
  assert.doesNotThrow(() => parseCaptionPayload(JSON.stringify({ ...transcript, original_text: null })));
  assert.throws(() => parseCaptionPayload(JSON.stringify({ ...transcript, original_text: 123 })));
  for (const source_revision of [0, -1, 1.5, "2", null]) {
    assert.throws(() => parseCaptionPayload(JSON.stringify({ ...transcript, source_revision })));
  }
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

test("accepts per-language incremental translation state", () => {
  const parsed = parseCaptionPayload(JSON.stringify({
    ...transcript, version: 3, stage: "translated",
    translations: { en: "Gospel" },
    translation_states: { en: "available", ja: "pending" },
  }));
  assert.equal(parsed.type, "transcript");
  if (parsed.type === "transcript") {
    assert.equal(parsed.translation_states?.ja, "pending");
  }
});

test("rejects invalid target-language status", () => {
  assert.throws(() => parseCaptionPayload(JSON.stringify({
    ...transcript, translation_states: { ko: "imaginary-status" },
  })), /caption event is invalid/);
});

const asrProvenance = {
  provider: "faster-whisper", configured_model: "small", verified_model: null,
  model_revision: null, timestamp_basis: "sample_clock_native_hint", sample_rate: 16000,
  audio_chunk_start_sample: 0, audio_chunk_end_sample: 64000, auto_generation: 1,
  timeline_offset_samples: 32000, replay_prefix_samples: 16000, overlap_trimmed: true,
};
const mtProvenance = {
  provider: "ollama", requested_model: null, configured_model: "qwen3.5:4b",
  verified_model: null, model_revision: null,
};

test("retains optional ASR timing/replay and independently failed target provenance", () => {
  const event = {
    ...transcript, asr_provenance: asrProvenance,
    translation_states: { en: "available", ja: "failed" }, translations: { en: "English" },
    translation_provenance: { en: mtProvenance, ja: mtProvenance },
  };
  assert.deepEqual(parseCaptionPayload(JSON.stringify(event)), event);
  assert.deepEqual(parseCaptionPayload(JSON.stringify({ type: "snapshot", segments: [event] })),
    { type: "snapshot", segments: [event] });
  assert.doesNotThrow(() => parseCaptionPayload(JSON.stringify({
    ...transcript, asr_provenance: null, translation_provenance: {},
  })));
});

test("rejects malformed, unbounded, private-location and fabricated alignment provenance", () => {
  for (const update of [
    { sample_rate: 0 }, { auto_generation: -1 }, { timeline_offset_samples: 2 ** 53 },
    { audio_chunk_start_sample: 64001 }, { replay_prefix_samples: "10" },
    { timestamp_basis: "native_aligned" }, { overlap_trimmed: null },
    { provider: "https://endpoint?secret" }, { configured_model: "/private/operator/model" },
    { verified_model: "C:\\private\\model" }, { model_revision: "x".repeat(201) },
  ]) {
    assert.throws(() => parseCaptionPayload(JSON.stringify({
      ...transcript, asr_provenance: { ...asrProvenance, ...update },
    })));
  }
  for (const update of [
    { requested_model: "https://host/model?token=secret" }, { provider: "user@host" },
    { configured_model: "C:secret" }, { model_revision: 123 },
    { requested_model: "sk-private-api-key" },
  ]) {
    assert.throws(() => parseCaptionPayload(JSON.stringify({
      ...transcript, translation_provenance: { en: { ...mtProvenance, ...update } },
    })));
  }
  assert.throws(() => parseCaptionPayload(JSON.stringify({
    ...transcript, translation_provenance: Object.fromEntries(
      Array.from({ length: 11 }, (_, index) => [`lang${index}`, mtProvenance]),
    ),
  })));
});
