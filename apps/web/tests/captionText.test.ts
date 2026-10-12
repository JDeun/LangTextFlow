import assert from "node:assert/strict";
import test from "node:test";
import { captionForLanguage } from "../src/captionText.ts";
import type { TranscriptEvent } from "../src/types.ts";

const original = {
  source_language: "ko",
  text: "하나님을 사랑합니다.",
  translations: {},
} as TranscriptEvent;

test("untranslated target caption is visibly labeled", () => {
  assert.match(captionForLanguage(original, "en", "Not translated"), /Not translated · KO/);
  assert.match(captionForLanguage(original, "en", "Not translated"), /하나님을 사랑합니다/);
});

test("translated and original captions do not receive untranslated label", () => {
  const translated = { ...original, translations: { en: "We love God." } } as TranscriptEvent;
  assert.equal(captionForLanguage(translated, "en", "Not translated"), "We love God.");
  assert.equal(captionForLanguage(original, "ko", "Not translated"), original.text);
});
