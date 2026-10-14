import assert from "node:assert/strict";
import test from "node:test";
import {
  parseTranslationCapabilities,
  providerBlockReason,
  type TranslationCapability,
  type TranslationRegistry,
} from "../src/translationCapabilities.ts";

const none: TranslationCapability = {
  provider: "none", label: "Source only", local_only: true,
  requires_model: false, configured: true, supported_pairs: [],
  field_qualified: false, glossary_mode: "unsupported", model_id: null, warnings: [],
};
const local: TranslationCapability = {
  provider: "m2m100-ct2", label: "M2M100", local_only: true,
  requires_model: true, configured: false,
  supported_pairs: [["ko", "en"], ["en", "ko"], ["ko", "ja"], ["ja", "ko"]],
  field_qualified: false, glossary_mode: "unsupported", model_id: "m2m100", warnings: [],
};
const remote: TranslationCapability = {
  ...local, provider: "openai-compatible", label: "Remote", local_only: false,
  configured: null, supported_pairs: null,
};
const demo: TranslationCapability = {
  ...none, provider: "demo", label: "Demo",
};
const registry: TranslationRegistry = { status: "ready", providers: [none, local, remote, demo] };

test("provider response rejects missing none, duplicates and malformed pairs", () => {
  assert.throws(() => parseTranslationCapabilities([]));
  assert.throws(() => parseTranslationCapabilities([local]));
  assert.throws(() => parseTranslationCapabilities([none, none]));
  assert.throws(() => parseTranslationCapabilities([none, { ...local, supported_pairs: ["ko-en"] }]));
  assert.equal(parseTranslationCapabilities([none, remote]).length, 2);
});

test("an unavailable registry fails closed except source-only captions", () => {
  const unavailable: TranslationRegistry = { status: "error", providers: [], message: "offline" };
  assert.equal(providerBlockReason(unavailable, "ollama", "ko", ["en"], "auto"), "registry");
  assert.equal(providerBlockReason(unavailable, "none", "ko", ["en"], "auto"), null);
});

test("uninstalled model is not silently offered, while unknown remote pairs stay unverified", () => {
  assert.equal(providerBlockReason(registry, "m2m100-ct2", "ko", ["en"], "auto"), "missing");
  assert.equal(providerBlockReason(registry, "openai-compatible", "ja", ["ko"], "auto"), null);
  assert.equal(providerBlockReason(registry, "nonexistent", "ko", ["en"], "auto"), "unknown");
});

test("support must cover every directed target without using source fallback", () => {
  const installed: TranslationRegistry = {
    status: "ready", providers: [none, { ...local, configured: true }],
  };
  assert.equal(providerBlockReason(installed, "m2m100-ct2", "ko", ["en", "ja"], "auto"), null);
  assert.equal(providerBlockReason(installed, "m2m100-ct2", "en", ["ja"], "auto"), "pair");
  assert.equal(providerBlockReason(installed, "m2m100-ct2", "ko", ["ko"], "auto"), "pair");
});

test("demo mode cannot be selected for actual ASR", () => {
  assert.equal(providerBlockReason(registry, "demo", "ko", ["en"], "auto"), "demo");
  assert.equal(providerBlockReason(registry, "demo", "ko", ["en"], "mock"), null);
});
