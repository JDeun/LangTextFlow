export type SummaryProvider = "ollama" | "openai-compatible";
export type SummaryLanguage = "ko" | "en" | "ja";

export function parseSummaryExternalProviders(value: unknown): SummaryProvider[] | null {
  if (!Array.isArray(value) || !value.includes("openai-compatible")
    || new Set(value).size !== value.length
    || value.some((provider) => provider !== "ollama" && provider !== "openai-compatible")) return null;
  return value as SummaryProvider[];
}

export function buildSummaryRequest(
  language: SummaryLanguage,
  provider: SummaryProvider,
  model: string,
  allowExternal: boolean,
  transcriptId: string | null = null,
  externalProviders: SummaryProvider[] | null = null,
) {
  if (!externalProviders) throw new Error("Refresh archive to verify provider transmission policy");
  const requiresApproval = externalProviders.includes(provider);
  if (requiresApproval && !allowExternal) {
    throw new Error("External summarization requires explicit approval");
  }
  return {
    language,
    provider,
    model: model.trim() || null,
    allow_external_provider: requiresApproval && allowExternal,
    ...(transcriptId ? { transcript_id: transcriptId } : {}),
  };
}
