import assert from "node:assert/strict";
import test from "node:test";
import { buildSummaryRequest } from "../src/summaryRequestPolicy.ts";

test("external provider cannot be invoked without explicit per-request approval", () => {
  assert.throws(() => buildSummaryRequest("ko", "openai-compatible", "gpt-test", false));
  assert.deepEqual(buildSummaryRequest("ja", "openai-compatible", "gpt-test", true), {
    language: "ja", provider: "openai-compatible", model: "gpt-test",
    allow_external_provider: true,
  });
});
test("local summary does not silently send external consent", () => {
  assert.deepEqual(buildSummaryRequest("en", "ollama", "  ", true), {
    language: "en", provider: "ollama", model: null,
    allow_external_provider: false,
  });
});
test("only an explicitly selected approved transcript is sent as the summary source", () => {
  assert.equal(buildSummaryRequest("ko", "ollama", "", false, "approved-version").transcript_id, "approved-version");
  assert.equal("transcript_id" in buildSummaryRequest("ko", "ollama", "", false), false);
});
