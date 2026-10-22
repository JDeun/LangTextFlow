import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  AUDIENCE_LATENCY_REPORTING_DEFAULT_ENABLED,
  createAudienceLatencyReport,
} from "../src/audienceLatencyReport.ts";

describe("privacy-safe audience latency report", () => {
  it("is disabled by default and requires explicit opt-in", () => {
    assert.equal(AUDIENCE_LATENCY_REPORTING_DEFAULT_ENABLED, false);
    assert.equal(createAudienceLatencyReport("translated", 20, false), null);
  });
  it("retains only stage, bounded latency, and timing method", () => {
    assert.deepEqual(createAudienceLatencyReport("translated", 20.6, true), {
      stage: "translated",
      receiveToDisplayMs: 21,
      timingMethod: "double_raf_estimate",
    });
  });
  it("rejects invalid stages and nonfinite or out-of-range values", () => {
    for (const value of [NaN, Infinity, -1, 60_001]) {
      assert.equal(createAudienceLatencyReport("translated", value, true), null);
    }
    assert.equal(createAudienceLatencyReport("unknown", 5, true), null);
  });
});
