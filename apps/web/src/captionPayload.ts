import type { CaptionStage, SnapshotEvent, TranscriptEvent } from "./types";

const CAPTION_STAGES = new Set<CaptionStage>([
  "partial",
  "stable",
  "corrected",
  "translated",
  "committed",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNonNegativeNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isStringMap(value: unknown): value is Record<string, string> {
  return (
    isRecord(value) &&
    Object.entries(value).every(
      ([key, item]) => key.length > 0 && typeof item === "string",
    )
  );
}

export function isTranscriptEvent(value: unknown): value is TranscriptEvent {
  if (!isRecord(value)) return false;
  if (value.type !== "transcript") return false;
  if (typeof value.segment_id !== "string" || value.segment_id.length === 0) return false;
  if (
    typeof value.version !== "number" ||
    !Number.isSafeInteger(value.version) ||
    value.version < 0
  ) {
    return false;
  }
  if (typeof value.stage !== "string" || !CAPTION_STAGES.has(value.stage as CaptionStage)) {
    return false;
  }
  if (typeof value.source_language !== "string" || value.source_language.length === 0) {
    return false;
  }
  if (typeof value.text !== "string" || !isStringMap(value.translations)) return false;

  const startMs = value.start_ms;
  const endMs = value.end_ms;
  if (!isFiniteNonNegativeNumber(startMs)) return false;
  if (endMs !== null && (!isFiniteNonNegativeNumber(endMs) || endMs < startMs)) {
    return false;
  }
  if (!isNullableString(value.speaker)) return false;
  if (
    value.confidence !== null &&
    (typeof value.confidence !== "number" ||
      !Number.isFinite(value.confidence) ||
      value.confidence < 0 ||
      value.confidence > 1)
  ) {
    return false;
  }
  if (value.correction !== null && !isRecord(value.correction)) return false;
  if (typeof value.committed !== "boolean") return false;
  if (typeof value.emitted_at !== "string" || value.emitted_at.length === 0) return false;
  return true;
}

export function parseCaptionPayload(raw: unknown): TranscriptEvent | SnapshotEvent | null {
  if (typeof raw !== "string") return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    return null;
  }

  if (!isRecord(parsed)) return null;
  if (parsed.type === "transcript") {
    return isTranscriptEvent(parsed) ? parsed : null;
  }
  if (parsed.type === "snapshot") {
    if (!Array.isArray(parsed.segments) || !parsed.segments.every(isTranscriptEvent)) {
      return null;
    }
    return parsed as SnapshotEvent;
  }
  return null;
}
