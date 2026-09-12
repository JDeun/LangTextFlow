export type CaptionStage =
  | "partial"
  | "stable"
  | "corrected"
  | "translated"
  | "committed";

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
  running: boolean;
  source_language: string;
  target_languages: string[];
  engine: string;
  started_at: string | null;
}
