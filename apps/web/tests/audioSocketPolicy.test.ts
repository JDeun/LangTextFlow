import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_AUDIO_SOCKET_BUFFER_BYTES,
  parseAudioSocketConfig,
  shouldSendAudioFrame,
} from "../src/audioSocketPolicy.ts";

test("accepts only the expected mono float32 audio config", () => {
  assert.deepEqual(
    parseAudioSocketConfig(
      JSON.stringify({
        type: "audio_config",
        sample_rate: 16_000,
        channels: 1,
        sample_format: "f32le",
      }),
    ),
    {
      type: "audio_config",
      sample_rate: 16_000,
      channels: 1,
      sample_format: "f32le",
    },
  );
});

test("rejects malformed and unsafe audio config payloads", () => {
  for (const raw of [
    "{bad-json",
    "null",
    JSON.stringify({ type: "audio_config", sample_rate: 0, channels: 1, sample_format: "f32le" }),
    JSON.stringify({ type: "audio_config", sample_rate: 384_000, channels: 1, sample_format: "f32le" }),
    JSON.stringify({ type: "audio_config", sample_rate: 16_000.5, channels: 1, sample_format: "f32le" }),
    JSON.stringify({ type: "audio_config", sample_rate: 16_000, channels: 2, sample_format: "f32le" }),
    JSON.stringify({ type: "audio_config", sample_rate: 16_000, channels: 1, sample_format: "pcm16" }),
  ]) {
    assert.equal(parseAudioSocketConfig(raw), null);
  }
  assert.equal(parseAudioSocketConfig(new ArrayBuffer(8)), null);
});

test("browser audio enqueue is bounded by websocket bufferedAmount", () => {
  assert.equal(shouldSendAudioFrame(0), true);
  assert.equal(shouldSendAudioFrame(MAX_AUDIO_SOCKET_BUFFER_BYTES), true);
  assert.equal(shouldSendAudioFrame(MAX_AUDIO_SOCKET_BUFFER_BYTES + 1), false);
  assert.equal(shouldSendAudioFrame(Number.POSITIVE_INFINITY), false);
  assert.equal(shouldSendAudioFrame(-1), false);
});
