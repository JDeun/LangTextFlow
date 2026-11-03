import type { ResolvedGlossarySnapshot, SessionContext } from "./types";

export interface GlossaryResolveRequest {
  source_language: string;
  target_languages?: string[];
  project_id: string | null;
  context: Pick<SessionContext, "preset" | "glossary">;
}

export function glossarySnapshotScope(request: GlossaryResolveRequest) {
  return JSON.stringify({
    source: request.source_language, project: request.project_id,
    preset: request.context.preset, overrides: request.context.glossary,
  });
}

export function parseGlossarySnapshot(value: unknown, request: GlossaryResolveRequest): ResolvedGlossarySnapshot {
  const snapshot = value as ResolvedGlossarySnapshot | null;
  if (!snapshot || typeof snapshot !== "object" || typeof snapshot.sha256 !== "string"
    || !/^[0-9a-f]{64}$/.test(snapshot.sha256)
    || snapshot.source_language !== request.source_language || snapshot.preset !== request.context.preset
    || snapshot.project_id !== request.project_id || !Array.isArray(snapshot.entries)
    || snapshot.entries.length > 500 || snapshot.entries.some(entry => !entry || typeof entry !== "object"
      || typeof entry.term !== "string" || !entry.term.trim() || entry.term.length > 160
      || entry.source_language !== request.source_language || entry.enabled !== true
      || typeof entry.category !== "string" || typeof entry.boost !== "number"
      || !Number.isFinite(entry.boost) || entry.boost < 0 || entry.boost > 20
      || !Array.isArray(entry.aliases) || entry.aliases.some(alias => typeof alias !== "string")
      || !entry.translations || typeof entry.translations !== "object" || Array.isArray(entry.translations)
      || Object.values(entry.translations).some(text => typeof text !== "string"))) {
    throw new Error("Invalid glossary snapshot");
  }
  return snapshot;
}
