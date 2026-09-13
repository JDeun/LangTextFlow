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

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isTranslations(value: unknown): value is Record<string, string> {
  if (!isRecord(value)) return false;
  return Object.entries(value).every(
    ([language, text]) => language.length > 0 && typeof text === "string",
  );
}

export function isTranscriptEvent(value: unknown): value is TranscriptEvent {
  if (!isRecord(value) || value.type !== "transcript") return false;
  if (typeof value.segment_id !== "string" || !value.segment_id) return false;
  if (!Number.isInteger(value.version) || (value.version as number) < 1) return false;
  if (typeof value.stage !== "string" || !CAPTION_STAGES.has(value.stage as CaptionStage)) {
    return false;
  }
  if (typeof value.source_language !== "string" || !value.source_language) return false;
  if (typeof value.text !== "string") return false;
  if (!isTranslations(value.translations)) return false;
  if (!Number.isInteger(value.start_ms) || (value.start_ms as number) < 0) return false;
  if (
    value.end_ms !== null &&
    (!Number.isInteger(value.end_ms) || (value.end_ms as number) < (value.start_ms as number))
  ) {
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
  if (typeof value.emitted_at !== "string" || !value.emitted_at) return false;
  return true;
}

export function parseCaptionPayload(raw: unknown): TranscriptEvent | SnapshotEvent {
  if (typeof raw !== "string") throw new Error("caption payload must be text JSON");

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("caption payload is not valid JSON");
  }

  if (!isRecord(value)) throw new Error("caption payload must be an object");
  if (value.type === "snapshot") {
    if (!Array.isArray(value.segments) || !value.segments.every(isTranscriptEvent)) {
      throw new Error("caption snapshot contains invalid segments");
    }
    return value as unknown as SnapshotEvent;
  }
  if (!isTranscriptEvent(value)) throw new Error("caption event is invalid");
  return value;
}
