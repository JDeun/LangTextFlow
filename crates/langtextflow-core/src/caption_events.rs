//! Canonical source, correction and independent-target events; no default cutover.
use crate::asr_slice::{AudioChunk, SourceEvent};
use crate::correction_slice::{CorrectionDecision, CorrectionOptions, CorrectionProvenance};
use crate::history::{HistorySink, Invalidation};
use serde::Serialize;
use serde_json::value::RawValue;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::Arc;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use tokio::sync::{mpsc, watch};

pub const MAX_OBSERVERS: usize = 16;
pub const OBSERVER_QUEUE: usize = 8;
pub const MAX_SNAPSHOT_BYTES: usize = 1024 * 1024;
const MAX_VISIBLE_SEGMENTS: usize = 100;
// Includes a transient 256-segment ASR reply plus the bounded correction/MT lanes.
const MAX_PENDING_SEGMENTS: usize = 512;
const MAX_PENDING_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_PUBLIC_IDS: usize = 128 * 256;
const SNAPSHOT_OVERHEAD: usize = 2048;
const JS_MAX: u64 = (1 << 53) - 1;
pub type JsonFrame = Arc<RawValue>;

pub struct ObservedConfig {
    pub source_language: String,
    pub targets: Vec<String>,
    pub(crate) configured_asr_model: Option<String>,
    token: String,
}

/// Local control-plane credential; never serialized into transcript/snapshot frames.
pub struct ObserverCredential(pub(crate) String);

impl ObserverCredential {
    pub fn path(&self) -> String {
        format!("/ws/observer?token={}", self.0)
    }
}

impl ObservedConfig {
    pub fn new(
        source: &str,
        targets: &[String],
    ) -> Result<(Self, ObserverCredential), &'static str> {
        if !matches!(source, "ko" | "en" | "ja")
            || targets.is_empty()
            || targets.len() > 2
            || targets
                .iter()
                .any(|lang| lang == source || !matches!(lang.as_str(), "ko" | "en" | "ja"))
            || (targets.len() == 2 && targets[0] == targets[1])
        {
            return Err("invalid_languages");
        }
        let mut bytes = [0_u8; 32];
        getrandom::fill(&mut bytes).map_err(|_| "random_unavailable")?;
        let token = bytes
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Ok((
            Self {
                source_language: source.into(),
                targets: targets.to_vec(),
                configured_asr_model: None,
                token: token.clone(),
            },
            ObserverCredential(token),
        ))
    }
}

#[derive(Clone, Serialize)]
pub(crate) struct AsrProvenance {
    pub(crate) provider: &'static str,
    pub(crate) configured_model: Option<String>,
    pub(crate) verified_model: Option<String>,
    pub(crate) model_revision: Option<String>,
    pub(crate) timestamp_basis: &'static str,
    pub(crate) sample_rate: Option<u32>,
    pub(crate) audio_chunk_start_sample: Option<u64>,
    pub(crate) audio_chunk_end_sample: Option<u64>,
    pub(crate) auto_generation: Option<u64>,
    pub(crate) timeline_offset_samples: Option<u64>,
    pub(crate) replay_prefix_samples: Option<u64>,
    pub(crate) overlap_trimmed: bool,
}

#[derive(Clone, Serialize)]
struct CaptionEvent {
    #[serde(rename = "type")]
    kind: &'static str,
    segment_id: String,
    version: u64,
    source_revision: u64,
    stage: &'static str,
    source_language: String,
    text: String,
    original_text: Option<String>,
    translations: BTreeMap<String, String>,
    translation_states: BTreeMap<String, &'static str>,
    asr_provenance: AsrProvenance,
    translation_provenance: BTreeMap<String, TranslationProvenance>,
    start_ms: u64,
    end_ms: Option<u64>,
    speaker: Option<String>,
    confidence: Option<f64>,
    correction: Option<CorrectionProvenance>,
    committed: bool,
    emitted_at: String,
    session_id: String,
    session_generation: u64,
    serial: u64,
}

impl CaptionEvent {
    fn source_text(&self) -> &str {
        self.original_text.as_deref().unwrap_or(&self.text)
    }
}

#[derive(Clone, Serialize)]
struct TranslationProvenance {
    provider: &'static str,
    requested_model: Option<String>,
    configured_model: Option<String>,
    verified_model: Option<String>,
    model_revision: Option<String>,
}

#[derive(Clone)]
pub struct ProjectedFrame {
    pub(crate) raw: JsonFrame,
    id: String,
    serial: u64,
    start_ms: u64,
    event: CaptionEvent,
    deterministic_text: Option<String>,
    llm_attempted: bool,
}

pub struct ObserverReceiver {
    pub(crate) id: u64,
    pub(crate) frames: mpsc::Receiver<JsonFrame>,
    pub(crate) control: watch::Receiver<bool>,
    pub(crate) telemetry: Option<watch::Receiver<Option<JsonFrame>>>,
}

struct Subscriber {
    frames: mpsc::Sender<JsonFrame>,
    control: watch::Sender<bool>,
}

#[derive(Default, Clone, Serialize)]
pub struct ObserverMetrics {
    pub capacity_rejections: u64,
    pub queue_evictions: u64,
    pub send_timeouts: u64,
    pub snapshot_evictions: u64,
    pub connected: usize,
}

#[derive(Default)]
pub(crate) struct CaptionProjection {
    pub(crate) history: Option<HistorySink>,
    pub(crate) config: Option<ObservedConfig>,
    latest: VecDeque<ProjectedFrame>,
    latest_bytes: usize,
    pending: HashMap<String, ProjectedFrame>,
    pending_bytes: usize,
    versions: HashMap<String, u64>,
    pub(crate) serial: u64,
    next_client: u64,
    subscribers: HashMap<u64, Subscriber>,
    pub(crate) metrics: ObserverMetrics,
    pub(crate) telemetry: Option<watch::Sender<Option<JsonFrame>>>,
}

impl CaptionProjection {
    pub(crate) fn reset(&mut self, config: Option<ObservedConfig>) {
        if let Some(history) = self.history.take() {
            history.close();
        }
        self.close_observers();
        self.telemetry = None;
        self.latest.clear();
        self.latest_bytes = 0;
        self.pending.clear();
        self.pending_bytes = 0;
        self.versions.clear();
        self.serial = 0;
        self.metrics.snapshot_evictions = 0;
        self.config = config;
    }

    pub(crate) fn close_observers(&mut self) {
        for (_, subscriber) in self.subscribers.drain() {
            let _ = subscriber.control.send(true);
        }
        self.metrics.connected = 0;
    }

    pub(crate) fn authorized(&self, token: &str) -> bool {
        self.config
            .as_ref()
            .is_some_and(|config| config.token == token)
    }

    pub(crate) fn prepare(
        &self,
        sid: &str,
        generation: u64,
        chunk: &AudioChunk,
        sources: &[SourceEvent],
        translating: bool,
        correcting: bool,
    ) -> Result<Vec<ProjectedFrame>, &'static str> {
        let origins = sources
            .iter()
            .map(|source| self.whisper_origin(chunk, source))
            .collect::<Vec<_>>();
        self.prepare_asr(sid, generation, sources, &origins, translating, correcting)
    }

    pub(crate) fn whisper_origin(&self, chunk: &AudioChunk, source: &SourceEvent) -> AsrProvenance {
        AsrProvenance {
            provider: "faster-whisper",
            configured_model: self
                .config
                .as_ref()
                .and_then(|c| c.configured_asr_model.clone()),
            verified_model: None,
            model_revision: None,
            timestamp_basis: source.timestamp_basis,
            sample_rate: Some(chunk.sample_rate),
            audio_chunk_start_sample: Some(chunk.start_sample),
            audio_chunk_end_sample: Some(chunk.end_sample),
            auto_generation: None,
            timeline_offset_samples: None,
            replay_prefix_samples: None,
            overlap_trimmed: false,
        }
    }

    pub(crate) fn prepare_asr(
        &self,
        sid: &str,
        generation: u64,
        sources: &[SourceEvent],
        origins: &[AsrProvenance],
        translating: bool,
        correcting: bool,
    ) -> Result<Vec<ProjectedFrame>, &'static str> {
        if origins.len() != sources.len() {
            return Err("source_provenance_count");
        }
        let Some(config) = &self.config else {
            return Ok(Vec::new());
        };
        if self.versions.len()
            + sources
                .iter()
                .filter(|source| !self.versions.contains_key(&source.segment_id))
                .count()
            > MAX_PUBLIC_IDS
        {
            return Err("version_capacity");
        }
        let mut prepared = Vec::new();
        let mut serial = self.serial;
        for (source_index, source) in sources.iter().enumerate() {
            let previous = self.versions.get(&source.segment_id).copied().unwrap_or(0);
            let stages: &[(&str, u64, bool)] = if translating || correcting {
                &[("stable", 1, false)]
            } else {
                &[("stable", 1, false), ("committed", 2, true)]
            };
            for &(stage, increment, committed) in stages {
                let version = previous
                    .checked_add(increment)
                    .filter(|v| *v <= JS_MAX)
                    .ok_or("version_exhausted")?;
                serial = serial
                    .checked_add(1)
                    .filter(|n| *n <= JS_MAX)
                    .ok_or("serial_exhausted")?;
                let event = CaptionEvent {
                    kind: "transcript",
                    segment_id: source.segment_id.clone(),
                    version,
                    source_revision: source.source_revision,
                    stage,
                    source_language: source.source_language.clone(),
                    text: source.text.clone(),
                    original_text: Some(source.text.clone()),
                    translations: BTreeMap::new(),
                    translation_states: config
                        .targets
                        .iter()
                        .map(|lang| {
                            (
                                lang.clone(),
                                if translating {
                                    "pending"
                                } else {
                                    "unsupported"
                                },
                            )
                        })
                        .collect(),
                    asr_provenance: origins[source_index].clone(),
                    translation_provenance: BTreeMap::new(),
                    start_ms: source.start_ms,
                    end_ms: Some(source.end_ms),
                    speaker: None,
                    confidence: None,
                    correction: None,
                    committed,
                    emitted_at: OffsetDateTime::now_utc()
                        .format(&Rfc3339)
                        .map_err(|_| "clock_format")?,
                    session_id: sid.into(),
                    session_generation: generation,
                    serial,
                };
                prepared.push(Self::frame(event)?);
            }
        }
        self.check_pending_limits(&prepared)?;
        Ok(prepared)
    }

    pub(crate) fn prepare_mock_partial(
        &self,
        sid: &str,
        generation: u64,
        source: &SourceEvent,
    ) -> Result<Vec<ProjectedFrame>, &'static str> {
        if self.versions.contains_key(&source.segment_id) {
            return Err("stale_revision");
        }
        if self.versions.len() >= MAX_PUBLIC_IDS {
            return Err("version_capacity");
        }
        let serial = self
            .serial
            .checked_add(1)
            .filter(|v| *v <= JS_MAX)
            .ok_or("serial_exhausted")?;
        Ok(vec![Self::frame(CaptionEvent {
            kind: "transcript",
            segment_id: source.segment_id.clone(),
            version: 1,
            source_revision: 1,
            stage: "partial",
            source_language: source.source_language.clone(),
            text: source.text.clone(),
            original_text: None,
            translations: BTreeMap::new(),
            translation_states: BTreeMap::new(),
            asr_provenance: Self::mock_origin(),
            translation_provenance: BTreeMap::new(),
            start_ms: source.start_ms,
            end_ms: None,
            speaker: None,
            confidence: Some(0.72),
            correction: None,
            committed: false,
            emitted_at: OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .map_err(|_| "clock_format")?,
            session_id: sid.into(),
            session_generation: generation,
            serial,
        })?])
    }

    pub(crate) fn prepare_mock_stable(
        &self,
        sid: &str,
        generation: u64,
        sources: &[SourceEvent],
        translating: bool,
        correcting: bool,
    ) -> Result<Vec<ProjectedFrame>, &'static str> {
        if sources
            .iter()
            .any(|source| !self.mock_partial_ready(&source.segment_id))
        {
            return Err("mock_partial_required");
        }
        let origins = sources
            .iter()
            .map(|_| Self::mock_origin())
            .collect::<Vec<_>>();
        self.prepare_asr(sid, generation, sources, &origins, translating, correcting)?
            .into_iter()
            .map(|frame| {
                let mut event = frame.event;
                event.confidence = Some(0.93);
                Self::frame(event)
            })
            .collect()
    }

    pub(crate) fn mock_partial_ready(&self, id: &str) -> bool {
        self.latest.iter().any(|frame| {
            frame.id == id
                && frame.event.stage == "partial"
                && frame.event.version == 1
                && frame.event.asr_provenance.provider == "mock"
        })
    }

    fn mock_origin() -> AsrProvenance {
        AsrProvenance {
            provider: "mock",
            configured_model: None,
            verified_model: None,
            model_revision: None,
            timestamp_basis: "synthetic",
            sample_rate: None,
            audio_chunk_start_sample: None,
            audio_chunk_end_sample: None,
            auto_generation: None,
            timeline_offset_samples: None,
            replay_prefix_samples: None,
            overlap_trimmed: false,
        }
    }

    fn frame(event: CaptionEvent) -> Result<ProjectedFrame, &'static str> {
        let raw =
            RawValue::from_string(serde_json::to_string(&event).map_err(|_| "event_encoding")?)
                .map_err(|_| "event_encoding")?;
        if raw.get().len() > MAX_SNAPSHOT_BYTES - SNAPSHOT_OVERHEAD {
            return Err("event_limit");
        }
        Ok(ProjectedFrame {
            raw: Arc::from(raw),
            id: event.segment_id.clone(),
            serial: event.serial,
            start_ms: event.start_ms,
            event,
            deterministic_text: None,
            llm_attempted: false,
        })
    }

    pub(crate) fn correction_progress(
        &mut self,
        id: &str,
        revision: u64,
        deterministic: &str,
        attempted: bool,
    ) -> bool {
        let Some(frame) = self.pending.get_mut(id) else {
            return false;
        };
        if frame.event.source_revision != revision || frame.event.stage != "stable" {
            return false;
        }
        frame.deterministic_text = Some(deterministic.into());
        frame.llm_attempted |= attempted;
        true
    }

    pub(crate) fn target(
        &mut self,
        id: &str,
        revision: u64,
        target: &str,
        outcome: crate::mt_slice::MtOutcome,
        deadline: std::time::Instant,
        cancelled: &std::sync::atomic::AtomicBool,
    ) -> Result<(), &'static str> {
        let visible = self.latest.iter().any(|frame| frame.id == id);
        let Some(previous) = self
            .pending
            .get(id)
            .or_else(|| self.latest.iter().find(|frame| frame.id == id))
        else {
            return Ok(());
        };
        if previous.event.source_revision != revision
            || previous.event.committed
            || previous.event.translation_states.get(target) != Some(&"pending")
        {
            return Err("stale_translation");
        }
        let mut event = previous.event.clone();
        event.version = event
            .version
            .checked_add(1)
            .filter(|v| *v <= JS_MAX)
            .ok_or("version_exhausted")?;
        event.serial = if visible {
            self.serial
                .checked_add(1)
                .filter(|v| *v <= JS_MAX)
                .ok_or("serial_exhausted")?
        } else {
            self.serial
        };
        let status = match outcome {
            crate::mt_slice::MtOutcome::Available(text) => {
                event.translations.insert(target.into(), text);
                event.translation_provenance.insert(
                    target.into(),
                    TranslationProvenance {
                        provider: "m2m100-ct2",
                        requested_model: None,
                        configured_model: None,
                        verified_model: None,
                        model_revision: None,
                    },
                );
                "available"
            }
            crate::mt_slice::MtOutcome::Failed => "failed",
            crate::mt_slice::MtOutcome::TimedOut => "timed_out",
        };
        event.translation_states.insert(target.into(), status);
        event.committed = event
            .translation_states
            .values()
            .all(|value| *value != "pending");
        event.stage = if event.committed {
            "committed"
        } else if event.correction.is_some() {
            "translated"
        } else {
            "stable"
        };
        event.emitted_at = OffsetDateTime::now_utc()
            .format(&Rfc3339)
            .map_err(|_| "clock_format")?;
        let mut prepared = Self::frame(event)?;
        // Clone/serialization also consumes the target budget. This is the
        // publication boundary; a late success becomes an explicit failure.
        if status == "available" {
            let expired = std::time::Instant::now() >= deadline;
            if expired || cancelled.load(std::sync::atomic::Ordering::Acquire) {
                let mut event = prepared.event;
                event.translations.remove(target);
                event.translation_provenance.remove(target);
                event
                    .translation_states
                    .insert(target.into(), if expired { "timed_out" } else { "failed" });
                prepared = Self::frame(event)?;
            }
        }
        self.apply_updates(vec![prepared], visible)?;
        Ok(())
    }

    pub(crate) fn corrected(
        &mut self,
        id: &str,
        revision: u64,
        mut decision: CorrectionDecision,
        options: &CorrectionOptions,
        deadline: std::time::Instant,
        cancelled: &std::sync::atomic::AtomicBool,
        translating: bool,
    ) -> Result<Option<String>, &'static str> {
        let visible = self.latest.iter().any(|frame| frame.id == id);
        let Some(previous) = self
            .pending
            .get(id)
            .or_else(|| self.latest.iter().find(|frame| frame.id == id))
        else {
            return Ok(None);
        };
        if previous.event.source_revision != revision
            || previous.event.committed
            || previous.event.stage != "stable"
            || cancelled.load(std::sync::atomic::Ordering::Acquire)
        {
            return Err("stale_correction");
        }
        if decision.provenance.method == "fallback" {
            if let Some(deterministic) = &previous.deterministic_text {
                decision = CorrectionDecision::fallback(
                    previous.event.source_text(),
                    deterministic,
                    options,
                    previous.llm_attempted || decision.provenance.llm_attempted,
                    decision
                        .provenance
                        .fallback_reason
                        .unwrap_or("correction provider unavailable"),
                );
            }
        }
        let mut event = previous.event.clone();
        event.version = self.versions[&event.segment_id]
            .checked_add(1)
            .filter(|v| *v <= JS_MAX)
            .ok_or("version_exhausted")?;
        event.serial = if visible {
            self.serial
                .checked_add(1)
                .filter(|v| *v <= JS_MAX)
                .ok_or("serial_exhausted")?
        } else {
            self.serial
        };
        event.stage = "corrected";
        event.text = decision.text;
        event.correction = Some(decision.provenance);
        event.emitted_at = OffsetDateTime::now_utc()
            .format(&Rfc3339)
            .map_err(|_| "clock_format")?;
        let prepared = Self::frame(event)?;
        let text = prepared.event.text.clone();
        let mut frames = vec![prepared];
        if !translating {
            let mut committed = frames[0].event.clone();
            committed.version = committed
                .version
                .checked_add(1)
                .filter(|v| *v <= JS_MAX)
                .ok_or("version_exhausted")?;
            if visible {
                committed.serial = committed
                    .serial
                    .checked_add(1)
                    .filter(|v| *v <= JS_MAX)
                    .ok_or("serial_exhausted")?;
            }
            committed.stage = "committed";
            committed.committed = true;
            frames.push(Self::frame(committed)?);
        }
        if cancelled.load(std::sync::atomic::Ordering::Acquire) {
            return Err("stale_correction");
        }
        if std::time::Instant::now() >= deadline
            && frames[0].event.correction.as_ref().unwrap().method != "fallback"
        {
            let event = &frames[0].event;
            let attempted = event.correction.as_ref().unwrap().llm_attempted;
            let fallback = CorrectionDecision::fallback(
                event.source_text(),
                if attempted {
                    &decision.deterministic_text
                } else {
                    event.source_text()
                },
                options,
                attempted,
                if attempted {
                    "correction timed out; using deterministic result"
                } else {
                    "correction provider unavailable"
                },
            );
            return self.corrected(
                id,
                revision,
                fallback,
                options,
                deadline,
                cancelled,
                translating,
            );
        }
        self.apply_updates(frames, visible)?;
        Ok(Some(text))
    }

    fn check_pending_limits(&self, prepared: &[ProjectedFrame]) -> Result<(), &'static str> {
        let extra = prepared
            .iter()
            .filter(|frame| !frame.event.committed && frame.event.stage != "partial");
        if self.pending.len()
            + extra
                .clone()
                .filter(|frame| !self.pending.contains_key(&frame.id))
                .count()
            > MAX_PENDING_SEGMENTS
            || self.pending_bytes + extra.map(|frame| frame.raw.get().len()).sum::<usize>()
                > MAX_PENDING_BYTES
        {
            return Err("pending_caption_capacity");
        }
        Ok(())
    }

    pub(crate) fn apply(&mut self, prepared: Vec<ProjectedFrame>) -> Result<(), &'static str> {
        self.apply_updates(prepared, true)
    }

    fn apply_updates(
        &mut self,
        prepared: Vec<ProjectedFrame>,
        visible: bool,
    ) -> Result<(), &'static str> {
        self.check_pending_limits(&prepared)?;
        for frame in prepared {
            if let Some(history) = &self.history {
                history.enqueue(frame.raw.clone());
            }
            self.versions.insert(frame.id.clone(), frame.event.version);
            if let Some(old) = self.pending.remove(&frame.id) {
                self.pending_bytes -= old.raw.get().len();
            }
            if !frame.event.committed && frame.event.stage != "partial" {
                self.pending_bytes += frame.raw.get().len();
                self.pending.insert(frame.id.clone(), frame.clone());
            }
            // Rolled-off pending sources still finish in SQLite. They allocate
            // no public serial and cannot resurrect in the bounded live view.
            if !visible {
                continue;
            }
            if let Some(position) = self.latest.iter().position(|old| old.id == frame.id) {
                self.latest_bytes -= self.latest.remove(position).unwrap().raw.get().len();
            }
            self.latest_bytes += frame.raw.get().len();
            self.serial = frame.serial;
            self.latest.push_back(frame.clone());
            while self.latest.len() > MAX_VISIBLE_SEGMENTS
                || self.latest_bytes > MAX_SNAPSHOT_BYTES - SNAPSHOT_OVERHEAD
            {
                self.latest_bytes -= self.latest.pop_front().unwrap().raw.get().len();
                self.metrics.snapshot_evictions += 1;
            }
            self.broadcast(frame.raw);
        }
        Ok(())
    }

    pub(crate) fn cancel_pending(
        &mut self,
        options: Option<&CorrectionOptions>,
    ) -> Result<(), &'static str> {
        let mut pending = self.pending.values().cloned().collect::<Vec<_>>();
        pending.sort_by(|left, right| (left.start_ms, &left.id).cmp(&(right.start_ms, &right.id)));
        for previous in pending {
            let visible = self.latest.iter().any(|frame| frame.id == previous.id);
            let mut event = previous.event;
            event.version = self.versions[&event.segment_id]
                .checked_add(1)
                .filter(|v| *v <= JS_MAX)
                .ok_or("version_exhausted")?;
            event.serial = if visible {
                self.serial
                    .checked_add(1)
                    .filter(|v| *v <= JS_MAX)
                    .ok_or("serial_exhausted")?
            } else {
                self.serial
            };
            event.stage = "committed";
            event.committed = true;
            for state in event.translation_states.values_mut() {
                if *state == "pending" {
                    *state = "failed";
                }
            }
            if event.correction.is_none() {
                if let Some(options) = options {
                    let fallback = CorrectionDecision::fallback(
                        event.source_text(),
                        previous
                            .deterministic_text
                            .as_deref()
                            .unwrap_or(&event.text),
                        options,
                        previous.llm_attempted,
                        "correction provider unavailable",
                    );
                    event.text = fallback.text;
                    event.correction = Some(fallback.provenance);
                }
            }
            event.emitted_at = OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .map_err(|_| "clock_format")?;
            self.apply_updates(vec![Self::frame(event)?], visible)?;
        }
        Ok(())
    }

    fn broadcast(&mut self, raw: JsonFrame) {
        self.subscribers.retain(|_, subscriber| {
            if subscriber.frames.try_send(raw.clone()).is_err() {
                let _ = subscriber.control.send(true);
                self.metrics.queue_evictions += 1;
                false
            } else {
                true
            }
        });
        self.metrics.connected = self.subscribers.len();
    }

    pub(crate) fn invalidate(
        &mut self,
        sid: &str,
        generation: u64,
        ids: &[String],
        revision: u64,
    ) -> Result<(), &'static str> {
        if ids.is_empty() || self.config.is_none() {
            return Ok(());
        }
        let retained = self
            .latest
            .iter()
            .filter(|frame| !ids.contains(&frame.id))
            .cloned()
            .collect::<VecDeque<_>>();
        let serial = self
            .serial
            .checked_add(1)
            .filter(|v| *v <= JS_MAX)
            .ok_or("serial_exhausted")?;
        let snapshot = self.snapshot(sid, generation, &retained, serial)?;
        if let Some(history) = &self.history {
            history.invalidate(
                ids.iter()
                    .filter_map(|id| {
                        self.versions.get(id).map(|version| Invalidation {
                            id: id.clone(),
                            version: *version,
                            revision,
                        })
                    })
                    .collect(),
            );
        }
        self.latest_bytes = retained.iter().map(|frame| frame.raw.get().len()).sum();
        self.latest = retained;
        for id in ids {
            if let Some(old) = self.pending.remove(id) {
                self.pending_bytes -= old.raw.get().len();
            }
        }
        self.serial = serial;
        // Existing snapshot schema is authoritative for removal. Keep per-ID
        // version history even when an obsolete/evicted event is not visible.
        self.broadcast(snapshot);
        Ok(())
    }

    fn snapshot(
        &self,
        sid: &str,
        generation: u64,
        latest: &VecDeque<ProjectedFrame>,
        serial: u64,
    ) -> Result<JsonFrame, &'static str> {
        #[derive(Serialize)]
        struct Snapshot<'a> {
            #[serde(rename = "type")]
            kind: &'static str,
            session_id: &'a str,
            session_generation: u64,
            serial: u64,
            source_language: &'a str,
            target_languages: &'a [String],
            segments: Vec<&'a RawValue>,
            snapshot_evictions: u64,
        }
        let config = self.config.as_ref().ok_or("unauthorized")?;
        let mut visible = latest.iter().collect::<Vec<_>>();
        visible.sort_by_key(|frame| (frame.start_ms, frame.serial));
        let json = serde_json::to_string(&Snapshot {
            kind: "snapshot",
            session_id: sid,
            session_generation: generation,
            serial,
            source_language: &config.source_language,
            target_languages: &config.targets,
            segments: visible
                .into_iter()
                .map(|frame| frame.raw.as_ref())
                .collect(),
            snapshot_evictions: self.metrics.snapshot_evictions,
        })
        .map_err(|_| "snapshot_encoding")?;
        if json.len() > MAX_SNAPSHOT_BYTES {
            return Err("snapshot_limit");
        }
        Ok(Arc::from(
            RawValue::from_string(json).map_err(|_| "snapshot_encoding")?,
        ))
    }

    pub(crate) fn subscribe(
        &mut self,
        sid: &str,
        generation: u64,
        token: &str,
    ) -> Result<ObserverReceiver, &'static str> {
        if !self.authorized(token) {
            return Err("unauthorized");
        }
        if self.subscribers.len() >= MAX_OBSERVERS {
            self.metrics.capacity_rejections += 1;
            return Err("observer_capacity");
        }
        let snapshot = self.snapshot(sid, generation, &self.latest, self.serial)?;
        self.next_client = self
            .next_client
            .checked_add(1)
            .ok_or("client_id_exhausted")?;
        let (tx, rx) = mpsc::channel(OBSERVER_QUEUE);
        tx.try_send(snapshot).map_err(|_| "snapshot_queue")?;
        let (control_tx, control) = watch::channel(false);
        self.subscribers.insert(
            self.next_client,
            Subscriber {
                frames: tx,
                control: control_tx,
            },
        );
        self.metrics.connected = self.subscribers.len();
        Ok(ObserverReceiver {
            id: self.next_client,
            frames: rx,
            control,
            telemetry: self.telemetry.as_ref().map(|sender| {
                let mut receiver = sender.subscribe();
                receiver.mark_changed();
                receiver
            }),
        })
    }

    pub(crate) fn remove(&mut self, id: u64, timeout: bool) {
        self.subscribers.remove(&id);
        self.metrics.connected = self.subscribers.len();
        if timeout {
            self.metrics.send_timeouts += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mt_slice::MtOutcome;
    use std::sync::atomic::AtomicBool;
    use std::time::{Duration, Instant};

    #[test]
    fn final_publication_gate_rejects_expired_or_cancelled_success_without_losing_peer() {
        for cancelled in [false, true] {
            let mut projection = CaptionProjection::default();
            let (config, _) = ObservedConfig::new("ko", &["ja".into(), "en".into()]).unwrap();
            projection.reset(Some(config));
            let source = SourceEvent {
                session_id: "session".into(),
                session_generation: 1,
                segment_id: "rs-1-0".into(),
                source_revision: 1,
                source_language: "ko".into(),
                stage: "stable",
                text: "Original".into(),
                start_ms: 0,
                end_ms: 1000,
                timestamp_basis: "sample_clock_native_hint",
            };
            let prepared = projection
                .prepare(
                    "session",
                    1,
                    &AudioChunk {
                        wav_path: "synthetic.wav".into(),
                        start_sample: 0,
                        end_sample: 16_000,
                        sample_rate: 16_000,
                    },
                    &[source],
                    true,
                    false,
                )
                .unwrap();
            projection.apply(prepared).unwrap();
            projection
                .target(
                    "rs-1-0",
                    1,
                    "ja",
                    MtOutcome::Available("Japanese".into()),
                    Instant::now() + Duration::from_secs(1),
                    &AtomicBool::new(false),
                )
                .unwrap();
            projection
                .target(
                    "rs-1-0",
                    1,
                    "en",
                    MtOutcome::Available("Late English".into()),
                    if cancelled {
                        Instant::now() + Duration::from_secs(1)
                    } else {
                        Instant::now() - Duration::from_secs(1)
                    },
                    &AtomicBool::new(cancelled),
                )
                .unwrap();
            let event = &projection.latest.back().unwrap().event;
            assert_eq!(
                event.translation_states["en"],
                if cancelled { "failed" } else { "timed_out" }
            );
            assert_eq!(event.translations.len(), 1);
            assert_eq!(event.translations["ja"], "Japanese");
            assert_eq!(event.translation_provenance.len(), 1);
            assert_eq!(event.original_text.as_deref(), Some("Original"));
            assert_eq!(event.version, 3);
            assert!(event.committed);
        }
    }

    #[test]
    fn nonterminal_history_cache_has_count_and_encoded_byte_bounds_and_is_released_on_cancel() {
        let chunk = AudioChunk {
            wav_path: "synthetic.wav".into(),
            start_sample: 0,
            end_sample: 16000,
            sample_rate: 16000,
        };
        let sources = |offset: usize, text: &str| {
            (0..256)
                .map(|index| SourceEvent {
                    session_id: "session".into(),
                    session_generation: 1,
                    segment_id: format!("rs-cap-{}", offset + index),
                    source_revision: 1,
                    source_language: "en".into(),
                    stage: "stable",
                    text: text.into(),
                    start_ms: 0,
                    end_ms: 1000,
                    timestamp_basis: "sample_clock_native_hint",
                })
                .collect::<Vec<_>>()
        };
        let mut projection = CaptionProjection::default();
        let (config, _) = ObservedConfig::new("en", &["ko".into()]).unwrap();
        projection.reset(Some(config));
        for offset in [0, 256] {
            let frames = projection
                .prepare("session", 1, &chunk, &sources(offset, "short"), true, false)
                .unwrap();
            projection.apply(frames).unwrap();
        }
        assert_eq!(projection.pending.len(), MAX_PENDING_SEGMENTS);
        assert!(projection
            .prepare("session", 1, &chunk, &sources(512, "short"), true, false)
            .is_err());
        projection.cancel_pending(None).unwrap();
        assert!(projection.pending.is_empty() && projection.pending_bytes == 0);
        assert!(projection.latest.len() <= 100 && projection.latest_bytes <= MAX_SNAPSHOT_BYTES);
        let (config, _) = ObservedConfig::new("en", &["ko".into()]).unwrap();
        projection.reset(Some(config));
        let long = "x".repeat(20_000);
        let frames = projection
            .prepare("session", 1, &chunk, &sources(0, &long), true, false)
            .unwrap();
        projection.apply(frames).unwrap();
        assert!(projection.pending_bytes > MAX_PENDING_BYTES / 2);
        assert!(projection
            .prepare("session", 1, &chunk, &sources(256, &long), true, false)
            .is_err());
        projection.cancel_pending(None).unwrap();
        assert_eq!(projection.pending_bytes, 0);
    }
}
