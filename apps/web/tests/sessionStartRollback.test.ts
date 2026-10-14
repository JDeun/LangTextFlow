import assert from "node:assert/strict";
import test from "node:test";
import { rollbackFailedSessionStart } from "../src/sessionStartRollback.ts";

test("preflight, device or rejected HTTP start must not stop another session", async () => {
  const actions: string[] = [];
  await rollbackFailedSessionStart(false, {
    stopCapture: async () => { actions.push("capture"); },
    stopRemote: async () => { actions.push("remote"); },
  });
  assert.deepEqual(actions, []);
});

test("a start request failure always rolls back capture and remote session", async () => {
  const actions: string[] = [];
  await rollbackFailedSessionStart(true, {
    stopCapture: async () => { actions.push("capture"); },
    stopRemote: async () => { actions.push("remote"); },
  });
  assert.deepEqual(actions, ["capture", "remote"]);
});

test("local microphone cleanup error cannot prevent remote rollback", async () => {
  const actions: string[] = [];
  await rollbackFailedSessionStart(true, {
    stopCapture: async () => {
      actions.push("capture");
      throw new Error("mic device unavailable");
    },
    stopRemote: async () => { actions.push("remote"); },
  });
  assert.deepEqual(actions, ["capture", "remote"]);
});

test("rollback endpoint error also preserves the original start failure", async () => {
  await assert.doesNotReject(rollbackFailedSessionStart(true, {
    stopCapture: async () => {},
    stopRemote: async () => { throw new Error("network unavailable"); },
  }));
});
