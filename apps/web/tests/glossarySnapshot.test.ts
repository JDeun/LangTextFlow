import assert from "node:assert/strict";
import test from "node:test";
import { parseGlossarySnapshot } from "../src/glossarySnapshot.ts";

const request = { source_language: "en", project_id: "project-a",
  context: { preset: "church" as const, glossary: [] } };
const valid = { source_language: "en", project_id: "project-a", preset: "church",
  sha256: "a".repeat(64), entries: [{ term: "Grace", source_language: "en", enabled: true,
    aliases: ["grace"], translations: { ko: "은혜" }, category: "theology", boost: 1.5 }] };

test("resolved glossary response binds its displayed scope and validates rendered data", () => {
  assert.deepEqual(parseGlossarySnapshot(valid, request), valid);
  for (const change of [{ project_id: "other" }, { source_language: "ko" },
    { preset: "lecture" }, { sha256: "invalid" }, { entries: [{ ...valid.entries[0], boost: {} }] },
    { entries: [{ ...valid.entries[0], aliases: [null] }] },
    { entries: [{ ...valid.entries[0], translations: { ko: {} } }] },
    { entries: Array(501).fill(valid.entries[0]) }]) {
    assert.throws(() => parseGlossarySnapshot({ ...valid, ...change }, request));
  }
});
