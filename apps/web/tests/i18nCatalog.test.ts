import assert from "node:assert/strict";
import test from "node:test";

import {
  SUPPORTED_LOCALES,
  normalizeLocale,
  resolveInitialLocale,
} from "../src/locales.ts";

test("ships the three launch interface locales in stable fallback order", () => {
  assert.deepEqual([...SUPPORTED_LOCALES], ["en", "ko", "ja"]);
  assert.equal(new Set(SUPPORTED_LOCALES).size, SUPPORTED_LOCALES.length);
});

test("normalizes browser language tags and falls back to English", () => {
  assert.equal(normalizeLocale("ko-KR"), "ko");
  assert.equal(normalizeLocale("ja-JP"), "ja");
  assert.equal(normalizeLocale("en-US"), "en");
  assert.equal(normalizeLocale("fr-FR"), null);
  assert.equal(resolveInitialLocale(null, "fr-FR"), "en");
});

test("explicit persisted locale wins over browser language", () => {
  assert.equal(resolveInitialLocale("ja", "ko-KR"), "ja");
});
