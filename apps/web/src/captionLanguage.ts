import type { TranscriptEvent } from "./types";

type CaptionContent = Pick<
  TranscriptEvent,
  "text" | "source_language" | "translations" | "translation_states" | "committed"
>;

const STATES: Record<string, [string, string]> = {
  ko: ["한국어 번역 중…", "한국어 번역을 사용할 수 없습니다."],
  en: ["Translating into English…", "English translation unavailable."],
  ja: ["日本語に翻訳中…", "日本語訳を利用できません。"],
};

// Never mistake a source-language ASR transcript for target-language MT output.
// Source transcription is shown ONLY when the source track is selected.
export function captionTextForLanguage(
  segment: CaptionContent | undefined,
  language: string,
  emptyText = "",
): string {
  if (!segment) return emptyText;
  if (language === segment.source_language) return segment.text;
  const translated = segment.translations[language]?.trim();
  if (translated) return translated;
  const current = segment.translation_states?.[language];
  const status = STATES[language] || [
    `Translating into ${language.toUpperCase()}…`,
    `Translation in ${language.toUpperCase()} unavailable.`,
  ];
  const unavailable = current === "failed" || current === "timed_out"
    || current === "unsupported";
  return status[segment.committed || unavailable ? 1 : 0];
}
