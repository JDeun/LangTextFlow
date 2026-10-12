import type { TranscriptEvent } from "./types";

/** Do not silently label untranslated source text as a target-language caption. */
export function captionForLanguage(
  segment: TranscriptEvent | undefined,
  language: string,
  untranslatedLabel: string,
  emptyText = "",
): string {
  if (!segment) return emptyText;
  if (language === segment.source_language) return segment.text;
  const translated = segment.translations[language];
  if (translated && translated.trim()) return translated;
  return `[${untranslatedLabel} · ${segment.source_language.toUpperCase()}] ${segment.text}`;
}
