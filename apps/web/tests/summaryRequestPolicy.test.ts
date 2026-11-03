import assert from "node:assert/strict";
import test from "node:test";
import { buildSummaryRequest, parseSummaryExternalProviders } from "../src/summaryRequestPolicy.ts";

const localPolicy = ["openai-compatible"] as const;
const request = (...args: Parameters<typeof buildSummaryRequest>) => buildSummaryRequest(
  args[0], args[1], args[2], args[3], args[4], args[5] ?? [...localPolicy],
);

test("external provider cannot be invoked without explicit per-request approval", () => {
  assert.throws(() => request("ko", "openai-compatible", "gpt-test", false));
  assert.deepEqual(request("ja", "openai-compatible", "gpt-test", true), {
    language: "ja", provider: "openai-compatible", model: "gpt-test",
    allow_external_provider: true,
  });
});
test("local summary does not silently send external consent", () => {
  assert.deepEqual(request("en", "ollama", "  ", true), {
    language: "en", provider: "ollama", model: null,
    allow_external_provider: false,
  });
});
test("only an explicitly selected approved transcript is sent as the summary source", () => {
  assert.equal(request("ko", "ollama", "", false, "approved-version").transcript_id, "approved-version");
  assert.equal("transcript_id" in request("ko", "ollama", "", false), false);
});

test("remote Ollama requires approval and forwards it only with verified metadata", () => {
  const remotePolicy = parseSummaryExternalProviders(["openai-compatible", "ollama"]);
  assert.throws(() => buildSummaryRequest("ko", "ollama", "test", false, null, remotePolicy));
  assert.equal(buildSummaryRequest("ko", "ollama", "test", true, null, remotePolicy).allow_external_provider, true);
});

test("missing or malformed policy fails closed even when a stale checkbox was checked", () => {
  for (const value of [undefined, null, {}, [], ["ollama"], ["openai-compatible", "unknown"],
    ["openai-compatible", "openai-compatible"]]) {
    const policy = parseSummaryExternalProviders(value);
    assert.equal(policy, null);
    assert.throws(() => buildSummaryRequest("ko", "ollama", "test", true, null, policy), /Refresh archive/);
  }
});
