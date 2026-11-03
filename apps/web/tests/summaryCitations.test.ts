import assert from "node:assert/strict";
import test from "node:test";
import { summaryCitations } from "../src/summaryCitations.ts";
import type { TranscriptEvent } from "../src/types.ts";

const source: TranscriptEvent = {
  type: "transcript", segment_id: "source-1", version: 3, source_revision: 1,
  stage: "committed", source_language: "ko", text: "교정 표시문", original_text: "ASR 원문",
  translations: { en: "Translated" }, start_ms: 12500, end_ms: 15000,
  committed: true, speaker: null, confidence: null, correction: null,
  emitted_at: "2026-11-03T00:00:00Z",
};

test("valid source citations link once despite repeated mentions", () => {
  assert.deepEqual(summaryCitations("Point [source-1 at 12s]. Again [source-1 at 12s].", [source]), [
    { segmentId: "source-1", seconds: 12 },
  ]);
});

test("unknown, retimed and uncommitted references cannot navigate to evidence", () => {
  assert.deepEqual(summaryCitations("[other-session at 12s] [source-1 at 13s]", [source]), []);
  assert.deepEqual(summaryCitations("[source-1 at 12s]", [{ ...source, committed: false }]), []);
});

test("unsafe identifiers are never interpreted as HTML or selectors", () => {
  assert.deepEqual(summaryCitations("[<script> at 12s] [source-1' at 12s]", [source]), []);
});
