import assert from "node:assert/strict";
import test from "node:test";

import {
  AUDIO_BACKPRESSURE_CLOSE_CODE,
  AUDIO_PROTOCOL_ERROR_CODE,
  MAX_AUDIO_SOCKET_BUFFERED_BYTES,
  canQueueAudioFrame,
  parseAudioSocketConfig,
} from "../src/audioCapturePolicy.ts";

test("audio buffering stays within the configured browser-side ceiling", () => {
  assert.equal(canQueueAudioFrame(0, 4096), true);
  assert.equal(canQueueAudioFrame(MAX_AUDIO_SOCKET_BUFFERED_BYTES, 0), true);
  assert.equal(canQueueAudioFrame(MAX_AUDIO_SOCKET_BUFFERED_BYTES, 1), false);
  assert.equal(
    canQueueAudioFrame(MAX_AUDIO_SOCKET_BUFFERED_BYTES - 4096, 4096),
    true,
  );
  assert.equal(
    canQueueAudioFrame(MAX_AUDIO_SOCKET_BUFFERED_BYTES - 4095, 4096),
    false,
  );
  assert.equal(canQueueAudioFrame(Number.NaN, 4), false);
});

test("client-selected websocket close codes are browser-valid application codes", () => {
  for (const code of [AUDIO_PROTOCOL_ERROR_CODE, AUDIO_BACKPRESSURE_CLOSE_CODE]) {
    assert.equal(code >= 3000 && code <= 4999, true);
  }
});

test("audio socket config parser accepts only supported mono f32le settings", () => {
  assert.deepEqual(
    parseAudioSocketConfig(
      JSON.stringify({
        type: "audio_config",
        sample_rate: 16000,
        channels: 1,
        sample_format: "f32le",
      }),
    ),
    {
      type: "audio_config",
      sample_rate: 16000,
      channels: 1,
      sample_format: "f32le",
    },
  );

  assert.throws(() => parseAudioSocketConfig("not-json"), /잘못된 설정 응답/);
  assert.throws(
    () =>
      parseAudioSocketConfig(
        JSON.stringify({
          type: "audio_config",
          sample_rate: 16000,
          channels: 2,
          sample_format: "f32le",
        }),
      ),
    /지원 범위/,
  );
});
