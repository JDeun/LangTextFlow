export type CaptionStage =
  | "partial"
  | "stable"
  | "corrected"
  | "translated"
  | "committed";

export type ProductPreset = "general" | "church" | "conference" | "lecture";
export type OutputMode = "operator" | "audience" | "projector" | "obs" | "overlay";
export type CaptionFontFamily = "system" | "sans" | "serif" | "mono";
export type CaptionTextAlign = "left" | "center";
export type PreflightStatus = "ready" | "warning" | "missing" | "error" | "info";
export type ModelSetupJobState = "queued" | "running" | "completed" | "cancelled" | "error";
export type VibeVoiceLifecycleMode =
  | "unconfigured"
  | "stopped"
  | "starting"
  | "managed"
  | "external"
  | "error";

export interface CaptionDisplaySettings {
  font_family: CaptionFontFamily;
  font_scale_percent: number;
  max_lines: number;
  hold_seconds: number;
  show_source_when_translated: boolean;
  text_align: CaptionTextAlign;
}

export interface GlossaryEntry {
  term: string;
  // Older exports and callers may omit this; backend assumes ko for legacy records.
  source_language?: string;
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

export interface ResolvedGlossarySnapshot {
  source_language: string;
  preset: ProductPreset;
  project_id: string | null;
  entries: GlossaryEntry[];
  sha256: string;
}

export interface ReferenceDocument {
  filename: string;
  media_type: string;
  size_bytes: number;
  content_base64?: string | null;
  text?: string;
  character_count?: number;
  truncated?: boolean;
  sha256?: string;
}

export interface SessionContext {
  title: string;
  presenter: string | null;
  preset: ProductPreset;
  description: string;
  hotwords: string[];
  glossary: GlossaryEntry[];
  reference_documents: ReferenceDocument[];
  reference_text: string;
  output_modes: OutputMode[];
  audience_access: boolean;
  display_settings: CaptionDisplaySettings;
}

export interface CorrectionProvenance {
  method: "deterministic" | "llm" | "fallback";
  provider: string | null;
  model: string | null;
  deterministic_changed: boolean;
  llm_attempted: boolean;
  llm_applied: boolean;
  changed: boolean;
  fallback_reason: string | null;
}

export type TranslationTargetState =
  | "pending" | "available" | "failed" | "timed_out" | "unsupported";

export interface AsrProvenance {
  provider: string;
  configured_model: string | null;
  verified_model: string | null;
  model_revision: string | null;
  timestamp_basis: "unknown" | "synthetic" | "sample_clock_native_hint" |
    "sample_clock_estimated" | "sequence_estimated";
  sample_rate: number | null;
  audio_chunk_start_sample: number | null;
  audio_chunk_end_sample: number | null;
  auto_generation: number | null;
  timeline_offset_samples: number | null;
  replay_prefix_samples: number | null;
  overlap_trimmed: boolean;
}

export interface TranslationProvenance {
  provider: string;
  requested_model: string | null;
  configured_model: string | null;
  verified_model: string | null;
  model_revision: string | null;
}

export interface TranscriptEvent {
  type: "transcript";
  segment_id: string;
  version: number;
  source_revision?: number;
  stage: CaptionStage;
  source_language: string;
  text: string;
  original_text?: string | null;
  translations: Record<string, string>;
  // Optional for old sessions and unchanged ASR adapters.
  translation_states?: Record<string, TranslationTargetState>;
  asr_provenance?: AsrProvenance | null;
  translation_provenance?: Record<string, TranslationProvenance>;
  start_ms: number;
  end_ms: number | null;
  speaker: string | null;
  confidence: number | null;
  correction: CorrectionProvenance | null;
  committed: boolean;
  emitted_at: string;
}

export interface SnapshotEvent {
  type: "snapshot";
  segments: TranscriptEvent[];
}

export interface ProviderStatus {
  enabled: boolean;
  provider: string;
  model: string | null;
  available: boolean;
  error: string | null;
}

export interface CorrectionStatus extends ProviderStatus {}
export interface TranslationStatus extends ProviderStatus {}

export interface SessionState {
  project_id?: string | null;
  resource_profile?: "standard" | "low";
  collect_audience_metrics?: boolean;
  session_id: string | null;
  join_code: string | null;
  running: boolean;
  source_language: string;
  target_languages: string[];
  engine: string;
  context: SessionContext | null;
  audio_required: boolean;
  audio_sample_rate: number | null;
  recording_enabled: boolean;
  recording_error: string | null;
  correction_status: CorrectionStatus;
  translation_status: TranslationStatus;
  persistence_error: string | null;
  started_at: string | null;
}

export interface EventProject {
  project_id: string;
  title: string;
  description: string;
  event_date: string | null;
  location: string;
  status: "planning" | "active" | "completed" | "archived";
  created_at: string;
  updated_at: string;
  session_count: number;
}

export interface SessionRecord {
  session_id: string;
  project_id: string | null;
  join_code: string;
  title: string;
  notes: string;
  presenter: string | null;
  preset: ProductPreset;
  source_language: string;
  target_languages: string[];
  engine: string;
  correction_provider: string;
  correction_model: string | null;
  translation_provider: string;
  translation_model: string | null;
  started_at: string;
  ended_at: string | null;
  interrupted: boolean;
  segment_count: number;
}

export interface SessionDetail extends SessionRecord {
  context: SessionContext;
}

export interface AudienceSessionView {
  collect_audience_metrics?: boolean;
  session_id: string;
  join_code: string;
  running: boolean;
  title: string;
  presenter: string | null;
  preset: ProductPreset;
  source_language: string;
  target_languages: string[];
  display_settings: CaptionDisplaySettings;
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
  details: Record<string, unknown>;
  error: string | null;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface VibeVoiceLifecycleState {
  mode: VibeVoiceLifecycleMode;
  configured: boolean;
  managed: boolean;
  running: boolean;
  healthy: boolean;
  pid: number | null;
  status: string;
  error: string | null;
  url: string;
  repo_path: string | null;
  model_path: string | null;
  started_at: string | null;
  log_tail: string[];
}

export interface RustQueueMetrics {
  queued: number;
  capacity: number;
  active: number;
  high: number;
  admitted: number;
  succeeded: number;
  failed: number;
  expired: number;
  cancelled: number;
  overflow: number;
  unavailable: number;
  wait_samples: number;
  wait_p50_ms: number | null;
  wait_p95_ms: number | null;
}

export interface RustAuthorityMetrics {
  status: "not_sampled" | "fresh" | "stale" | "final" | "unavailable";
  age_ms: number | null;
  sample: {
    session_id: string;
    session_generation: number;
    sequence: number;
    sampled_at: string;
    final: boolean;
    stop_error: string | null;
    correction: RustQueueMetrics | null;
    translation: RustQueueMetrics | null;
  } | null;
}

export interface RealtimeMetrics {
  runtime_core?: "python" | "rust";
  rust_authority?: RustAuthorityMetrics | null;
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
  asr_last_failover_audio_ms: number | null;
  asr_queue_depth: number | null;
  asr_queue_capacity: number | null;
  asr_queue_high_watermark: number | null;
  persistence_queue_depth: number | null;
  persistence_queue_capacity: number | null;
  persistence_queue_high_watermark: number | null;
  persistence_events_dropped: number | null;
  persistence_events_failed: number | null;
  postprocess_queue_depth: number | null;
  postprocess_queue_capacity: number | null;
  postprocess_queue_lag_p50_ms: number | null;
  postprocess_queue_lag_p95_ms: number | null;
  websocket_clients: number;
  websocket_client_capacity: number;
  websocket_clients_rejected: number;
  websocket_overflow_evictions: number;
  websocket_send_failures: number;
  websocket_client_queue_high_watermark: number;
  postprocess_queue_high_watermark: number | null;
  postprocess_segments_dropped: number | null;
  postprocess_segments_failed: number | null;
  postprocess_segments_expired: number | null;
  postprocess_duplicate_asr_events_dropped: number | null;
  postprocess_revised_asr_events_rejected?: number | null;
  stale_asr_events_dropped: number | null;
  last_asr_lag_ms: number | null;
  last_correction_latency_ms: number | null;
  last_translation_latency_ms: number | null;
  last_commit_latency_ms: number | null;
  last_event_at: string | null;
}
