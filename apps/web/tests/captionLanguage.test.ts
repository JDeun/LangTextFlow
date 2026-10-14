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

test("a new segment never inherits the previous segment's translated caption", () => {
  const previous = {
    ...initial,
    segment_id: "old",
    committed: true,
    translations: { ko: "예수님은 우리의 구주이십니다." },
  };
  const next = {
    ...initial,
    segment_id: "new",
    committed: false,
    text: "Grace is a gift",
    translations: {},
  };
  assert.equal(captionTextForLanguage(previous, "ko"), "예수님은 우리의 구주이십니다.");
  assert.equal(captionTextForLanguage(next, "ko"), "한국어 번역 중…");
  assert.equal(
    captionTextForLanguage({ ...next, committed: true }, "ko"),
    "한국어 번역을 사용할 수 없습니다.",
  );
});

test("late translation becomes visible only on its own language track", () => {
  const before = { ...initial, translations: { ja: "救い" }, committed: false };
  assert.equal(captionTextForLanguage(before, "ko"), "한국어 번역 중…");
  assert.equal(captionTextForLanguage(before, "ja"), "救い");
  const after = { ...before, translations: { ja: "救い", ko: "구원" } };
  assert.equal(captionTextForLanguage(after, "ko"), "구원");
});

test("empty and whitespace-only translations are not treated as successful outputs", () => {
  const event = {
    ...initial,
    committed: true,
    translations: { ko: "   ", ja: "\t" },
  };
  assert.equal(captionTextForLanguage(event, "ko"), "한국어 번역을 사용할 수 없습니다.");
  assert.equal(captionTextForLanguage(event, "ja"), "日本語訳を利用できません。");
  assert.equal(captionTextForLanguage(event, "en"), initial.text);
});

test("language failure becomes unavailable while other target remains pending", () => {
  const segment = {
    ...initial,
    translations: {},
    translation_states: { ko: "failed", ja: "pending" } as const,
  };
  assert.equal(captionTextForLanguage(segment, "ko"), "한국어 번역을 사용할 수 없습니다.");
  assert.equal(captionTextForLanguage(segment, "ja"), "日本語に翻訳中…");
});

test("available partial translation remains visible before other language completes", () => {
  const segment = {
    ...initial,
    translations: { ko: "예수님은 구주이십니다" },
    translation_states: { ko: "available", ja: "pending" } as const,
  };
  assert.equal(captionTextForLanguage(segment, "ko"), "예수님은 구주이십니다");
  assert.equal(captionTextForLanguage(segment, "ja"), "日本語に翻訳中…");
});
