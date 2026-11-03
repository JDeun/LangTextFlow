import assert from "node:assert/strict";
import test from "node:test";
import { recordingAtSessionTime } from "../src/recordingAlignment.ts";

const recordings = [
  { recording_id: "legacy", duration_seconds: 100 },
  { recording_id: "import", duration_seconds: 100, start_offset_ms: null },
  { recording_id: "early", duration_seconds: .5, start_offset_ms: 1000 },
  { recording_id: "late", duration_seconds: .75, start_offset_ms: 1750 },
];
test("late and resumed recordings map absolute citations to the correct file-local time", () => {
  assert.deepEqual(recordingAtSessionTime(recordings, 1250), { id: "early", startMs: 250 });
  assert.deepEqual(recordingAtSessionTime(recordings, 2000), { id: "late", startMs: 250 });
  assert.deepEqual(recordingAtSessionTime(recordings, 1750), { id: "late", startMs: 0 });
});
test("unrecorded intervals and legacy/import timing never silently assume an offset of zero", () => {
  for (const time of [0, 500, 1500, 1700, 2500, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.equal(recordingAtSessionTime(recordings, time), null);
  }
});
test("known zero is valid, and invalid metadata cannot authorize a seek", () => {
  assert.deepEqual(recordingAtSessionTime([{ recording_id: "known", duration_seconds: 1, start_offset_ms: 0 }], 500), { id: "known", startMs: 500 });
  assert.equal(recordingAtSessionTime([{ recording_id: "bad", duration_seconds: 1, start_offset_ms: Number.NaN }], 500), null);
});
