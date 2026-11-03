import type { CaptionStage, SnapshotEvent, TranscriptEvent } from "./types";

const TARGET_STATES = new Set([
  "pending", "available", "failed", "timed_out", "unsupported",
]);

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

function isPublicModel(value: unknown): boolean {
  return value === null || (typeof value === "string" && value.length <= 200 && value.trim() === value &&
    /^[A-Za-z0-9][A-Za-z0-9_.:-]*(?:\/[A-Za-z0-9][A-Za-z0-9_.:-]*)?$/.test(value) &&
    !value.includes("..") && !value.includes(":/") && !/^[A-Za-z]:/.test(value) &&
    !/^(sk-|hf_|ghp_|github_pat_|xoxb-|xoxp-)/.test(value));
}

function isModelProvenance(value: unknown): value is Record<string, unknown> {
  return isRecord(value) && typeof value.provider === "string" &&
    value.provider.trim() === value.provider &&
    /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$/.test(value.provider) &&
    ["configured_model", "verified_model", "model_revision"].every(
      (key) => isPublicModel(value[key]),
    );
}

function isAsrProvenance(value: unknown): boolean {
  if (value === null) return true;
  if (!isModelProvenance(value)) return false;
  if (!["unknown", "synthetic", "sample_clock_native_hint", "sample_clock_estimated",
    "sequence_estimated"].includes(value.timestamp_basis as string)) return false;
  const bounded = (key: string, max: number, min = 0) => value[key] === null ||
    (Number.isSafeInteger(value[key]) && (value[key] as number) >= min &&
      (value[key] as number) <= max);
  if (!bounded("sample_rate", 384000, 1) || !bounded("auto_generation", 1000000) ||
    !["audio_chunk_start_sample", "audio_chunk_end_sample", "timeline_offset_samples",
      "replay_prefix_samples"].every((key) => bounded(key, Number.MAX_SAFE_INTEGER))) return false;
  if (value.audio_chunk_start_sample !== null && value.audio_chunk_end_sample !== null &&
    (value.audio_chunk_end_sample as number) < (value.audio_chunk_start_sample as number)) return false;
  return typeof value.overlap_trimmed === "boolean";
}

export function isTranscriptEvent(value: unknown): value is TranscriptEvent {
  if (!isRecord(value) || value.type !== "transcript") return false;
  if (typeof value.segment_id !== "string" || !value.segment_id) return false;
  if (!Number.isInteger(value.version) || (value.version as number) < 1) return false;
  if (value.source_revision !== undefined && (
    !Number.isInteger(value.source_revision) || (value.source_revision as number) < 1
  )) return false;
  if (typeof value.stage !== "string" || !CAPTION_STAGES.has(value.stage as CaptionStage)) {
    return false;
  }
  if (typeof value.source_language !== "string" || !value.source_language) return false;
  if (typeof value.text !== "string") return false;
  if (value.original_text !== undefined && !isNullableString(value.original_text)) return false;
  if (!isTranslations(value.translations)) return false;
  if (value.translation_states !== undefined && (
    !isRecord(value.translation_states) ||
    !Object.entries(value.translation_states).every(
      ([language, state]) =>
        language.length > 0 && typeof state === "string" && TARGET_STATES.has(state),
    )
  )) return false;
  if (value.asr_provenance !== undefined && !isAsrProvenance(value.asr_provenance)) return false;
  if (value.translation_provenance !== undefined && (
    !isRecord(value.translation_provenance) || Object.keys(value.translation_provenance).length > 10 ||
    !Object.entries(value.translation_provenance).every(([language, provenance]) =>
      language.length >= 2 && language.length <= 16 && isModelProvenance(provenance) &&
      isPublicModel(provenance.requested_model))
  )) return false;
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
