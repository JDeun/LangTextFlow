import assert from "node:assert/strict";
import test from "node:test";

import { resolveApiUrl } from "../src/apiPolicy.ts";

const loc = (protocol: string, hostname: string) => ({ protocol, hostname }) as Location;

test("explicit API URL wins and trims a trailing slash", () => {
  assert.equal(
    resolveApiUrl("https://example.test/v1/", loc("https:", "example.test")),
    "https://example.test/v1",
  );
});

test("Tauri Windows origin always uses loopback backend", () => {
  assert.equal(
    resolveApiUrl(undefined, loc("http:", "tauri.localhost")),
    "http://127.0.0.1:8000",
  );
});

test("Tauri custom protocol always uses loopback backend", () => {
  assert.equal(
    resolveApiUrl(undefined, loc("tauri:", "localhost")),
    "http://127.0.0.1:8000",
  );
});

test("browser audience resolves backend on current host", () => {
  assert.equal(
    resolveApiUrl(undefined, loc("https:", "captions.local")),
    "https://captions.local:8000",
  );
});
