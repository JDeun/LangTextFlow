import assert from "node:assert/strict";
import test from "node:test";
import { observeOperatorHeader } from "../src/operatorHeaderLayout.ts";

test("header observation avoids duplicate writes and stale route cleanup", (t) => {
  const nativeObserver = globalThis.ResizeObserver;
  const observers: { resize: () => void; disconnected: boolean }[] = [];
  globalThis.ResizeObserver = class {
    state;
    constructor(resize: () => void) {
      this.state = { resize, disconnected: false };
      observers.push(this.state);
    }
    observe() {}
    disconnect() { this.state.disconnected = true; }
  } as unknown as typeof ResizeObserver;
  t.after(() => { globalThis.ResizeObserver = nativeObserver; });

  let writes = 0;
  let value: string | undefined;
  let measured = false;
  const shell = {
    style: {
      setProperty: (_key: string, next: string) => { value = next; writes++; },
      removeProperty: () => { value = undefined; },
    },
    setAttribute: () => { measured = true; },
    removeAttribute: () => { measured = false; },
  };
  const header = (height: number) => ({ parentElement: shell, getBoundingClientRect: () => ({ height }) }) as unknown as HTMLElement;
  const oldCleanup = observeOperatorHeader(header(80));
  assert.equal(value, "80px");
  observers[0].resize();
  assert.equal(writes, 1);

  const newCleanup = observeOperatorHeader(header(180));
  oldCleanup?.();
  observers[0].resize(); // A queued callback from the previous route is harmless.
  assert.equal(value, "180px");
  assert.equal(measured, true);
  assert.equal(observers[0].disconnected, true);
  newCleanup?.();
  assert.equal(value, undefined);
  assert.equal(measured, false);
  assert.equal(observers[1].disconnected, true);
});
