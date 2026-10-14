/** Truthful ASR declarations from the backend, not quality guarantees. */
export interface AsrCapability {
  provider: string;
  label: string;
  implementation: "chunked" | "streamed_sidecar" | "synthetic" | "fallback";
  emitted_stages: ("partial" | "stable")[];
  timestamp_origin: "chunk_segment" | "sequence_estimate" | "synthetic" | "dynamic";
  hotwords: "native_hint" | "prompt_hint" | "unsupported" | "dynamic";
  word_timestamps: boolean;
  declared_languages: string[];
  model_language_quality_verified: boolean;
  native_session_cancellation_verified: boolean;
  warnings: string[];
}
export type AsrRegistry =
  | { status: "loading"; providers: [] }
  | { status: "error"; providers: []; message: string }
  | { status: "ready"; providers: AsrCapability[] };
const types = new Set(["chunked", "streamed_sidecar", "synthetic", "fallback"]);
const clocks = new Set(["chunk_segment", "sequence_estimate", "synthetic", "dynamic"]);
const hotwordModes = new Set(["native_hint", "prompt_hint", "unsupported", "dynamic"]);
export function parseAsrCapabilities(value: unknown): AsrCapability[] {
  if (!Array.isArray(value) || !value.length) throw new Error("ASR provider registry missing");
  const results: AsrCapability[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new Error("ASR provider registry malformed");
    }
    const itemRecord = item as Record<string, unknown>;
    if (typeof itemRecord.provider !== "string" || !/^[a-z][a-z0-9-]*$/.test(itemRecord.provider)
      || typeof itemRecord.label !== "string" || !itemRecord.label.trim()
      || !types.has(String(itemRecord.implementation))
      || !clocks.has(String(itemRecord.timestamp_origin))
      || !hotwordModes.has(String(itemRecord.hotwords))
      || typeof itemRecord.word_timestamps !== "boolean"
      || typeof itemRecord.model_language_quality_verified !== "boolean"
      || typeof itemRecord.native_session_cancellation_verified !== "boolean"
      || !Array.isArray(itemRecord.emitted_stages)
      || !itemRecord.emitted_stages.length
      || !itemRecord.emitted_stages.every((s: unknown) => s === "partial" || s === "stable")
      || !Array.isArray(itemRecord.declared_languages)
      || !itemRecord.declared_languages.length
      || !itemRecord.declared_languages.every((s: unknown) => typeof s === "string")
      || !Array.isArray(itemRecord.warnings)
      || !itemRecord.warnings.every((s: unknown) => typeof s === "string")
      || seen.has(itemRecord.provider)) {
      throw new Error("ASR provider metadata invalid or duplicated");
    }
    seen.add(itemRecord.provider);
    results.push(itemRecord as unknown as AsrCapability);
  }
  return results;
}

export type AsrBlockReason = "registry" | "unknown" | "language" | null;
export function asrBlockReason(
  registry: AsrRegistry, engine: string, sourceLanguage: string,
): AsrBlockReason {
  if (registry.status !== "ready") return "registry";
  const capability = registry.providers.find((row) => row.provider === engine);
  if (!capability) return "unknown";
  return capability.declared_languages.includes(sourceLanguage) ? null : "language";
}
