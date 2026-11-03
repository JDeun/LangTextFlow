import assert from "node:assert/strict";
import test from "node:test";
import { parseOperatorSessionState } from "../src/operatorSessionState.ts";

const provider = { enabled: false, provider: "none", model: null, available: true, error: null };
const state = {
  session_id: "active", join_code: "JOIN", running: true,
  source_language: "en", target_languages: ["ko", "ja"], engine: "faster-whisper",
  project_id: "project", resource_profile: "low", audio_required: true,
  recording_enabled: true, recording_error: null, collect_audience_metrics: true,
  context: null, correction_status: provider, translation_status: provider,
};

test("operator state retains server configuration while consent remains hook-owned", () => {
  assert.equal(parseOperatorSessionState(state), state);
});

test("malformed state cannot authorize replacing controls or stopping capture", () => {
  for (const malformed of [null, [], {}, { ...state, running: "false" },
    { ...state, session_id: null }, { ...state, target_languages: [] },
    { ...state, target_languages: ["en"] }, { ...state, resource_profile: "unknown" },
    { ...state, project_id: {} }, { ...state, translation_status: null },
    { ...state, correction_status: { ...provider, model: {} } },
    { ...state, context: { title: "incomplete" } },
    { ...state, collect_audience_metrics: "true" }]) {
    assert.throws(() => parseOperatorSessionState(malformed));
  }
});
