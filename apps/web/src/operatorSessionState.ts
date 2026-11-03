import type { SessionState } from "./types";

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const nullableText = (value: unknown) => value === null || typeof value === "string";
const texts = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(item => typeof item === "string");

/** Validate server state before it can replace live controls or stop local capture. */
export function parseOperatorSessionState(value: unknown): SessionState {
  if (!object(value) || typeof value.running !== "boolean"
    || !nullableText(value.session_id) || (value.running && !value.session_id)
    || typeof value.source_language !== "string" || !value.source_language.trim()
    || !texts(value.target_languages) || !value.target_languages.length
    || value.target_languages.some(target => !target.trim() || target === value.source_language)
    || typeof value.engine !== "string" || !value.engine.trim()
    || typeof value.audio_required !== "boolean" || typeof value.recording_enabled !== "boolean"
    || (value.collect_audience_metrics !== undefined && typeof value.collect_audience_metrics !== "boolean")
    || !nullableText(value.recording_error) || !nullableText(value.join_code)
    || (value.project_id !== undefined && !nullableText(value.project_id))
    || (value.resource_profile !== undefined && !["standard", "low"].includes(String(value.resource_profile)))) {
    throw new Error("Invalid operator session state");
  }
  for (const key of ["correction_status", "translation_status"]) {
    const status = value[key];
    if (!object(status) || typeof status.enabled !== "boolean"
      || typeof status.provider !== "string" || !nullableText(status.model)
      || typeof status.available !== "boolean" || !nullableText(status.error)) {
      throw new Error("Invalid operator provider state");
    }
  }
  if (value.context !== null) {
    const context = value.context;
    if (!object(context) || typeof context.title !== "string" || !nullableText(context.presenter)
      || !["church", "general", "conference", "lecture"].includes(String(context.preset))
      || !texts(context.hotwords) || !Array.isArray(context.reference_documents)
      || !context.reference_documents.every(document => object(document)
        && typeof document.filename === "string" && typeof document.media_type === "string"
        && typeof document.size_bytes === "number" && Number.isFinite(document.size_bytes)
        && (document.text === undefined || typeof document.text === "string"))) {
      throw new Error("Invalid operator context");
    }
    const display = context.display_settings;
    if (!object(display) || !["system", "sans", "serif", "mono"].includes(String(display.font_family))
      || !["left", "center"].includes(String(display.text_align))
      || typeof display.show_source_when_translated !== "boolean"
      || !["font_scale_percent", "max_lines", "hold_seconds"].every(key =>
        typeof display[key] === "number" && Number.isFinite(display[key]))) {
      throw new Error("Invalid operator display settings");
    }
  }
  return value as unknown as SessionState;
}
