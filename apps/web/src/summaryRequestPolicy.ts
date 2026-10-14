export type SummaryProvider = "ollama" | "openai-compatible";
export type SummaryLanguage = "ko" | "en" | "ja";

export function buildSummaryRequest(
  language: SummaryLanguage,
  provider: SummaryProvider,
  model: string,
  allowExternal: boolean,
) {
  if (provider === "openai-compatible" && !allowExternal) {
    throw new Error("External summarization requires explicit approval");
  }
  return {
    language,
    provider,
    model: model.trim() || null,
    allow_external_provider: provider === "openai-compatible" && allowExternal,
  };
}
