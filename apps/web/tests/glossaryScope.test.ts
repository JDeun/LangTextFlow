import assert from "node:assert/strict";
import test from "node:test";
import { visibleGlossaryRows } from "../src/glossaryScope.ts";

test("an old project glossary projection is never shown after a scope switch", () => {
  const first = { id: "secret-A", term: "project A term" };
  const oldProjection = { scope: "/projects/A/glossary", rows: [first] };
  assert.deepEqual(visibleGlossaryRows(oldProjection, "/projects/A/glossary"), [first]);
  assert.deepEqual(visibleGlossaryRows(oldProjection, "/projects/B/glossary"), []);
  assert.deepEqual(visibleGlossaryRows(oldProjection, "/glossary"), []);
});

test("a newer same-scope projection is displayed without reading another scope", () => {
  const newer = { scope: "/projects/B/glossary", rows: [{ id: "b" }] };
  assert.deepEqual(visibleGlossaryRows(newer, "/projects/B/glossary"), newer.rows);
  assert.deepEqual(visibleGlossaryRows(newer, "/projects/A/glossary"), []);
});
