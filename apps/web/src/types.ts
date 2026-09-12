export type CaptionStage =
  | "partial"
  | "stable"
  | "corrected"
  | "translated"
  | "committed";

export type ProductPreset = "general" | "church" | "conference" | "lecture";
export type OutputMode = "operator" | "audience" | "projector" | "obs" | "overlay";

export interface GlossaryEntry {
  term: string;
  aliases: string[];
  translations: Record<string, string>;
  category: string;
  boost: number;
  enabled: boolean;
}

export interface SessionContext {
  title: string;
  presenter: string | null;
  preset: ProductPreset;
  description: string;
  hotwords: string[];
  glossary: GlossaryEntry[];
  output_modes: OutputMode[];
  audience_access: boolean;
}

export interface TranscriptEvent {
  type: "transcript";
  segment_id: string;
  version: number;
  stage: CaptionStage;
  source_language: string;
  text: string;
  translations: Record<string, string>;
  start_ms: number;
  end_ms: number | null;
  speaker: string | null;
  confidence: number | null;
  committed: boolean;
  emitted_at: string;
}

export interface SnapshotEvent {
  type: "snapshot";
  segments: TranscriptEvent[];
}

export interface SessionState {
  session_id: string | null;
  join_code: string | null;
  running: boolean;
  source_language: string;
  target_languages: string[];
  engine: string;
  context: SessionContext | null;
  started_at: string | null;
}

export interface AudienceSessionView {
  session_id: string;
  join_code: string;
  running: boolean;
  title: string;
  presenter: string | null;
  preset: ProductPreset;
  source_language: string;
  target_languages: string[];
  started_at: string | null;
}
