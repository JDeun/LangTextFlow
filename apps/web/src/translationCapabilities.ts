/** Provider declarations from the local operator-only API: metadata, not field-quality proof. */
export interface TranslationCapability {
  provider: string;
  label: string;
  local_only: boolean;
  requires_model: boolean;
  supported_pairs: [string, string][] | null;
  configured: boolean | null;
  field_qualified: boolean;
  glossary_mode: string;
  model_id: string | null;
  warnings: string[];
}

export type TranslationRegistry =
  | { status: "loading"; providers: [] }
  | { status: "error"; providers: []; message: string }
  | { status: "ready"; providers: TranslationCapability[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Reject malformed/ambiguous API metadata instead of silently inventing capabilities. */
export function parseTranslationCapabilities(payload: unknown): TranslationCapability[] {
  if (!Array.isArray(payload) || payload.length === 0) {
    throw new Error("Translation provider registry is empty or malformed");
  }
  const providers: TranslationCapability[] = [];
  const seen = new Set<string>();
  for (const item of payload) {
    if (!isRecord(item)
      || typeof item.provider !== "string" || !/^[a-z][a-z0-9-]*$/.test(item.provider)
      || typeof item.label !== "string" || !item.label.trim()
      || typeof item.local_only !== "boolean"
      || typeof item.requires_model !== "boolean"
      || typeof item.field_qualified !== "boolean"
      || typeof item.glossary_mode !== "string"
      || !(item.configured === null || typeof item.configured === "boolean")
      || !(item.model_id === null || typeof item.model_id === "string")
      || !Array.isArray(item.warnings)
      || !item.warnings.every((entry: unknown) => typeof entry === "string")
      || !(item.supported_pairs === null || (Array.isArray(item.supported_pairs)
        && item.supported_pairs.every((pair: unknown) => Array.isArray(pair)
          && pair.length === 2 && pair.every((code: unknown) => typeof code === "string"))))) {
      throw new Error("Invalid translation capability declaration");
    }
    if (seen.has(item.provider)) throw new Error("Duplicate translation capability");
    seen.add(item.provider);
    providers.push(item as unknown as TranslationCapability);
  }
  if (!seen.has("none")) throw new Error("Translation provider registry lacks 'none'");
  return providers;
}

export type ProviderBlockReason = "registry" | "unknown" | "demo" | "missing" | "pair" | null;

export function providerBlockReason(
  registry: TranslationRegistry,
  provider: string,
  source: string,
  targets: string[],
  engine: string,
): ProviderBlockReason {
  // Source-only sessions are always safe even when capability discovery is down.
  if (provider === "none") return null;
  if (registry.status !== "ready") return "registry";
  const capability = registry.providers.find((item) => item.provider === provider);
  if (!capability) return "unknown";
  if (provider === "demo" && engine !== "mock") return "demo";
  if (capability.configured === false) return "missing";
  if (capability.supported_pairs !== null && targets.some((target) =>
    target === source || !capability.supported_pairs!.some(([src, dst]) =>
      src === source && dst === target))) return "pair";
  return null;
}
