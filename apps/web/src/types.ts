export type CaptionStage =
  | "partial"
  | "stable"
  | "corrected"
  | "translated"
  | "committed";

export type ProductPreset = "general" | "church" | "conference" | "lecture";
export type OutputMode = "operator" | "audience" | "projector" | "obs" | "overlay";
export type PreflightStatus = "ready" | "warning" | "missing" | "error" | "info";
export type ModelSetupJobState = "queued" | "running" | "completed" | "cancelled" | "error";

export interface GlossaryEntry {
  term: string;
  aliases: string[];
  translations: Record<string, string>;
  category: string;
  presets: ProductPreset[];
  boost: number;
  enabled: boolean;
}

export interface GlossaryRecord extends GlossaryEntry {
  id: string;
  created_at: string;
  updated_at: string;
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

export interface TranslationStatus {
  enabled: boolean;
  provider: string;
  model: string | null;
  available: boolean;
  error: string | null;
}

export interface SessionState {
  session_id: string | null;
  join_code: string | null;
  running: boolean;
  source_language: string;
  target_languages: string[];
  engine: string;
  context: SessionContext | null;
  audio_required: boolean;
  audio_sample_rate: number | null;
  translation_status: TranslationStatus;
  persistence_error: string | null;
  started_at: string | null;
}

export interface SessionRecord {
  session_id: string;
  join_code: string;
  title: string;
  presenter: string | null;
  preset: ProductPreset;
  source_language: string;
  target_languages: string[];
  engine: string;
  translation_provider: string;
  translation_model: string | null;
  started_at: string;
  ended_at: string | null;
  segment_count: number;
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

export interface NetworkInfo {
  addresses: string[];
  frontend_port: number;
  backend_port: number;
}

export interface PreflightCheck {
  id: string;
  label: string;
  status: PreflightStatus;
  summary: string;
  details: Record<string, unknown>;
  recommendation: string | null;
}

export interface RecommendedConfiguration {
  engine: string | null;
  translation_provider: string;
  translation_model: string | null;
  reasons: string[];
}

export interface SystemPreflight {
  generated_at: string;
  requested_engine: string;
  translation_provider: string;
  translation_model: string | null;
  ready: boolean;
  blocking_checks: string[];
  platform: string;
  architecture: string;
  python_version: string;
  cpu_count: number | null;
  memory_gb: number | null;
  disk_free_gb: number | null;
  checks: PreflightCheck[];
  recommended: RecommendedConfiguration;
}

export interface ModelSetupJob {
  job_id: string;
  provider: string;
  model: string;
  state: ModelSetupJobState;
  status: string;
  digest: string | null;
  completed_bytes: number | null;
  total_bytes: number | null;
  progress_percent: number | null;
  error: string | null;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface RealtimeMetrics {
  audio_frames_received: number;
  audio_bytes_received: number;
  audio_duration_ms: number;
  audio_rms_dbfs: number | null;
  voice_active: boolean;
  last_audio_enqueue_wait_ms: number | null;
  audio_backpressure_events: number;
  asr_provider: string | null;
  asr_running: boolean;
  asr_failure: string | null;
  asr_failover_count: number;
  asr_last_failover_reason: string | null;
  asr_queue_depth: number;
  asr_queue_capacity: number;
  asr_queue_high_watermark: number;
  persistence_queue_depth: number;
  persistence_queue_capacity: number;
  postprocess_queue_depth: number;
  postprocess_queue_capacity: number;
  last_asr_lag_ms: number | null;
  last_correction_latency_ms: number | null;
  last_translation_latency_ms: number | null;
  last_commit_latency_ms: number | null;
  last_event_at: string | null;
}
