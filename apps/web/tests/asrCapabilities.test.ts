import assert from "node:assert/strict";
import test from "node:test";
import {
  parseAsrCapabilities, asrBlockReason,
  type AsrCapability, type AsrRegistry,
} from "../src/asrCapabilities.ts";

const auto: AsrCapability = {
  provider: "auto", label: "Fallback",
  implementation: "fallback", emitted_stages: ["stable"],
  timestamp_origin: "dynamic", hotwords: "dynamic", word_timestamps: false,
  declared_languages: ["ko", "en", "ja"], warnings: ["No verified field quality"],
  model_language_quality_verified: false,
  native_session_cancellation_verified: false,
};
const registry: AsrRegistry = { status: "ready", providers: [auto] };

test("ASR registry refuses malformed, duplicate or untruthful metadata", () => {
  assert.throws(() => parseAsrCapabilities([]));
  assert.throws(() => parseAsrCapabilities([auto, auto]));
  assert.throws(() => parseAsrCapabilities([{ ...auto, emitted_stages: ["committed"] }]));
  assert.throws(() => parseAsrCapabilities([{ ...auto, declared_languages: [] }]));
  assert.deepEqual(parseAsrCapabilities([auto]), [auto]);
});

test("unknown provider and unavailable registry fail closed", () => {
  assert.equal(asrBlockReason(registry, "unknown", "ko"), "unknown");
  assert.equal(asrBlockReason({
    status: "error", providers: [], message: "network",
  }, "auto", "ko"), "registry");
});

test("ASR source language must be explicitly declared by provider", () => {
  assert.equal(asrBlockReason(registry, "auto", "ja"), null);
  assert.equal(asrBlockReason(registry, "auto", "fr"), "language");
});
