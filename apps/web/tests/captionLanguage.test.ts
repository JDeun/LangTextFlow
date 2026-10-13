import assert from "node:assert/strict";
import test from "node:test";
import { captionTextForLanguage } from "../src/captionLanguage.ts";

const initial = {
  source_language: "en",
  text: "Jesus is our Savior",
  translations: {} as Record<string, string>,
  committed: false,
};

test("visiting English missionary without KO translation never displays English as Korean", () => {
  const shown = captionTextForLanguage(initial, "ko");
  assert.equal(shown, "한국어 번역 중…");
  assert.notEqual(shown, initial.text);
  assert.equal(captionTextForLanguage(initial, "en"), initial.text);
});

test("completed untranslated English sermon reports Korean unavailable", () => {
  assert.equal(
    captionTextForLanguage({ ...initial, committed: true }, "ko"),
    "한국어 번역을 사용할 수 없습니다.",
  );
});

test("Japanese missionary and Korean congregation receive Korean translation", () => {
  const sample = {
    ...initial,
    source_language: "ja",
    text: "恵みがあります",
    translations: { ko: "은혜가 있습니다" },
  };
  assert.equal(captionTextForLanguage(sample, "ko"), "은혜가 있습니다");
  assert.equal(captionTextForLanguage(sample, "ja"), "恵みがあります");
});

test("Korean sermon with missing Japanese track is not displayed as Japanese", () => {
  const sample = { ...initial, source_language: "ko", text: "안녕하세요" };
  assert.equal(captionTextForLanguage(sample, "ja"), "日本語に翻訳中…");
  assert.equal(captionTextForLanguage(sample, "en"), "Translating into English…");
});

test("available language targets remain independent", () => {
  const sample = {
    ...initial, source_language: "ko",
    translations: { en: "Salvation", ja: "救い" },
  };
  assert.equal(captionTextForLanguage(sample, "en"), "Salvation");
  assert.equal(captionTextForLanguage(sample, "ja"), "救い");
  assert.equal(captionTextForLanguage(undefined, "ko", "No segment"), "No segment");
});
