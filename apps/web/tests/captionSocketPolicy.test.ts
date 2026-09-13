import assert from "node:assert/strict";
import test from "node:test";

import {
  BASE_RETRY_MS,
  MAX_RETRY_MS,
  isTerminalCloseCode,
  retryAfterMs,
  retryDelayMs,
  terminalMessage,
} from "../src/captionSocketPolicy.ts";

test("terminal close codes do not retry", () => {
  assert.equal(isTerminalCloseCode(4403), true);
  assert.equal(isTerminalCloseCode(4404), true);
  assert.equal(isTerminalCloseCode(4429), false);
  assert.equal(isTerminalCloseCode(1006), false);
});

test("rate-limit reason is converted to milliseconds", () => {
  assert.equal(retryAfterMs("retry after 8s"), 8_000);
  assert.equal(retryAfterMs("Retry After 0s"), 1_000);
  assert.equal(retryAfterMs("try later"), null);
});

test("retry backoff grows, jitters, and remains capped", () => {
  assert.equal(retryDelayMs(0, 0.5), BASE_RETRY_MS);
  assert.equal(retryDelayMs(1, 0.5), 1_500);
  assert.equal(retryDelayMs(2, 0.5), 3_000);
  assert.equal(retryDelayMs(20, 0.5), MAX_RETRY_MS);
  assert.equal(retryDelayMs(1, 0), 1_200);
  assert.equal(retryDelayMs(1, 1), 1_800);
});

test("terminal errors preserve server reason with safe fallbacks", () => {
  assert.equal(terminalMessage(4403, "origin denied"), "origin denied");
  assert.equal(terminalMessage(4404, ""), "세션을 찾을 수 없거나 종료되었습니다.");
  assert.equal(terminalMessage(1006, "network"), "");
});
