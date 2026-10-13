import type { GlossaryEntry, GlossaryRecord, ProductPreset } from "./types";

export const GLOSSARY_PRESETS: ProductPreset[] = ["general", "church", "conference", "lecture"];

export interface GlossaryDraft {
  term: string;
  aliases: string;
  en: string;
  ja: string;
  category: string;
  presets: ProductPreset[];
  globalScope: boolean;
  boost: number;
  enabled: boolean;
}

export function newGlossaryDraft(preset: ProductPreset): GlossaryDraft {
  return {
    term: "", aliases: "", en: "", ja: "",
    category: "general", presets: [preset], globalScope: false,
    boost: 1.5, enabled: true,
  };
}

export function existingGlossaryDraft(entry: GlossaryRecord): GlossaryDraft {
  return {
    term: entry.term, aliases: entry.aliases.join(", "),
    en: entry.translations.en ?? "", ja: entry.translations.ja ?? "",
    category: entry.category, presets: [...entry.presets],
    globalScope: entry.presets.length === 0,
    boost: entry.boost, enabled: entry.enabled,
  };
}

export function glossaryPayload(
  draft: GlossaryDraft,
  existing?: GlossaryRecord,
): GlossaryEntry {
  if (!draft.term.trim()) throw new Error("A glossary term is required");
  if (!draft.globalScope && draft.presets.length === 0) {
    throw new Error("Choose at least one preset");
  }
  // Keep translations for languages beyond EN/JA while editing an existing term.
  const translations: Record<string, string> = { ...existing?.translations };
  for (const language of ["en", "ja"] as const) {
    const value = draft[language].trim();
    if (value) translations[language] = value;
    else delete translations[language];
  }
  return {
    term: draft.term.trim(),
    aliases: [...new Set(draft.aliases.split(/[,\n]/).map(s => s.trim()).filter(Boolean))],
    translations,
    category: draft.category.trim() || "general",
    presets: draft.globalScope ? [] : [...new Set(draft.presets)],
    boost: draft.boost,
    enabled: draft.enabled,
  };
}
