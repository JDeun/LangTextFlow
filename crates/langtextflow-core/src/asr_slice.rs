//! First ASR vertical slice: Rust admission and immutable inference-result acceptance.
//! It is opt-in and does not replace the production Python caption runtime.
use crate::caption_events::{
    CaptionProjection, ObservedConfig, ObserverCredential, ObserverMetrics, ObserverReceiver,
};
use crate::{validate_reply, TaskKind, WorkerReply, WorkerRequest, WorkerStatus, PROTOCOL_VERSION};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex, Weak,
};

const SAMPLE_RATE: u32 = 16_000;
const MAX_CHUNK_SAMPLES: u64 = 120 * SAMPLE_RATE as u64;
const MAX_SEGMENTS: usize = 128;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioChunk {
    pub wav_path: String,
    pub start_sample: u64,
    pub end_sample: u64,
    pub sample_rate: u32,
}

impl AudioChunk {
    fn valid(&self) -> bool {
        self.sample_rate == SAMPLE_RATE
            && !self.wav_path.is_empty()
            && self.wav_path.len() <= 4096
            && self.end_sample > self.start_sample
            && self.end_sample - self.start_sample <= MAX_CHUNK_SAMPLES
            && self.end_sample <= (1_u64 << 53) / 1000
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InferredSegment {
    pub start_seconds: f64,
    pub end_seconds: f64,
    pub text: String,
    pub estimated_timestamps: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AsrResult {
    pub sample_count: u64,
    pub sample_rate: u32,
    pub segments: Vec<InferredSegment>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceEvent {
    pub session_id: String,
    pub session_generation: u64,
    pub segment_id: String,
    pub source_revision: u64,
    pub source_language: String,
    pub stage: &'static str,
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub timestamp_basis: &'static str,
}

pub struct Admission {
    request: WorkerRequest,
    pub cancelled: Arc<AtomicBool>,
    chunk: AudioChunk,
    owner: Weak<Mutex<State>>,
    deadline: std::time::Instant,
    source_id: u64,
}

impl Admission {
    pub fn request(&self) -> &WorkerRequest {
        &self.request
    }
    pub fn deadline(&self) -> std::time::Instant {
        self.deadline
    }
}

impl Drop for Admission {
    fn drop(&mut self) {
        self.cancelled.store(true, Ordering::Release);
        if let Some(owner) = self.owner.upgrade() {
            let mut state = owner.lock().unwrap();
            if state.active.as_ref().map(|active| &active.request_id)
                == Some(&self.request.request_id)
            {
                state.active = None;
            }
        }
        // The independent process permit stays leased until the child is reaped.
    }
}

struct Active {
    request_id: String,
    segment_id: String,
    cancelled: Arc<AtomicBool>,
}

struct State {
    session_id: String,
    generation: u64,
    serial: u64,
    revisions: HashMap<String, SegmentVersion>,
    active: Option<Active>,
    accepted: HashMap<String, Vec<SourceEvent>>,
    projection: CaptionProjection,
}

struct SegmentVersion {
    revision: u64,
    source_id: u64,
}

pub(crate) struct AsrObserver {
    pub(crate) receiver: ObserverReceiver,
    owner: Weak<Mutex<State>>,
}

impl Drop for AsrObserver {
    fn drop(&mut self) {
        if let Some(owner) = self.owner.upgrade() {
            owner
                .lock()
                .unwrap()
                .projection
                .remove(self.receiver.id, false);
        }
    }
}

/// Clones refer to the same Rust-owned authority; workers never receive it.
#[derive(Clone)]
pub struct AsrSession(Arc<Mutex<State>>);

impl Default for AsrSession {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(State {
            session_id: String::new(),
            generation: 0,
            serial: 0,
            revisions: HashMap::new(),
            active: None,
            accepted: HashMap::new(),
            projection: CaptionProjection::default(),
        })))
    }
}

impl AsrSession {
    pub fn start(&self, session_id: &str) -> Result<(), &'static str> {
        self.start_configured(session_id, None)
    }

    pub fn start_observed(
        &self,
        session_id: &str,
        source: &str,
        targets: &[String],
    ) -> Result<ObserverCredential, &'static str> {
        let (config, token) = ObservedConfig::new(source, targets)?;
        self.start_configured(session_id, Some(config))?;
        Ok(token)
    }

    fn start_configured(
        &self,
        session_id: &str,
        config: Option<ObservedConfig>,
    ) -> Result<(), &'static str> {
        if session_id.is_empty() || session_id.len() > 128 {
            return Err("invalid_session");
        }
        let mut state = self.0.lock().unwrap();
        let next_generation = state
            .generation
            .checked_add(1)
            .filter(|value| *value < (1_u64 << 53))
            .ok_or("generation_exhausted")?;
        if let Some(active) = &state.active {
            active.cancelled.store(true, Ordering::Release);
        }
        state.generation = next_generation;
        state.session_id = session_id.into();
        state.revisions.clear();
        state.accepted.clear();
        state.projection.reset(config);
        // Admission is retained until the previous request is finished/reaped.
        Ok(())
    }

    pub fn stop(&self) {
        let mut state = self.0.lock().unwrap();
        if let Some(active) = &state.active {
            active.cancelled.store(true, Ordering::Release);
        }
        state.session_id.clear();
        state.revisions.clear();
        state.accepted.clear();
        state.projection.reset(None);
    }

    pub fn observer_metrics(&self) -> ObserverMetrics {
        self.0.lock().unwrap().projection.metrics.clone()
    }

    pub(crate) fn authorize_observer(&self, token: &str) -> bool {
        self.0.lock().unwrap().projection.authorized(token)
    }

    pub(crate) fn subscribe_observer(&self, token: &str) -> Result<AsrObserver, &'static str> {
        let mut state = self.0.lock().unwrap();
        let (sid, generation) = (state.session_id.clone(), state.generation);
        let receiver = state.projection.subscribe(&sid, generation, token)?;
        Ok(AsrObserver {
            receiver,
            owner: Arc::downgrade(&self.0),
        })
    }

    pub(crate) fn observer_send_timeout(&self, id: u64) {
        self.0.lock().unwrap().projection.remove(id, true);
    }
    pub(crate) fn observer_capacity_rejected(&self) {
        self.0
            .lock()
            .unwrap()
            .projection
            .metrics
            .capacity_rejections += 1;
    }

    pub fn revise(&self, segment_id: &str, revision: u64) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.accepted.contains_key(segment_id) {
            return Err("accepted_chunk");
        }
        let old = state
            .revisions
            .get_mut(segment_id)
            .ok_or("unknown_segment")?;
        if revision <= old.revision || revision > (1_u64 << 53) - 1 {
            return Err("stale_revision");
        }
        old.revision = revision;
        if let Some(active) = &state.active {
            if active.segment_id == segment_id {
                active.cancelled.store(true, Ordering::Release);
            }
        }
        Ok(())
    }

    pub fn admit(
        &self,
        segment_id: &str,
        revision: u64,
        source_language: &str,
        chunk: AudioChunk,
        deadline_ms: u64,
    ) -> Result<Admission, &'static str> {
        if !chunk.valid()
            || !matches!(source_language, "ko" | "en" | "ja")
            || segment_id.is_empty()
            || segment_id.len() > 128
            || revision == 0
            || deadline_ms == 0
            || deadline_ms > 120_000
        {
            return Err("invalid_request");
        }
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty() {
            return Err("no_session");
        }
        if state.active.is_some() {
            return Err("busy");
        }
        if revision > (1_u64 << 53) - 1
            || state
                .projection
                .config
                .as_ref()
                .is_some_and(|config| config.source_language != source_language)
        {
            return Err("invalid_request");
        }
        if state
            .revisions
            .get(segment_id)
            .is_some_and(|old| revision < old.revision)
            || state.accepted.contains_key(segment_id)
        {
            return Err("stale_revision");
        }
        if !state.revisions.contains_key(segment_id) && state.revisions.len() >= MAX_SEGMENTS {
            return Err("segment_capacity");
        }
        state.serial = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
        let source_id = state
            .revisions
            .get(segment_id)
            .map_or(state.serial, |old| old.source_id);
        let request = WorkerRequest {
            protocol_version: PROTOCOL_VERSION,
            request_id: format!("r{}", state.serial),
            session_id: state.session_id.clone(),
            session_generation: state.generation,
            segment_id: segment_id.into(),
            source_revision: revision,
            task_kind: TaskKind::Asr,
            source_language: source_language.into(),
            target_language: None,
            deadline_ms,
            payload: serde_json::to_string(&chunk).map_err(|_| "invalid_payload")?,
        };
        let cancelled = Arc::new(AtomicBool::new(false));
        state.active = Some(Active {
            request_id: request.request_id.clone(),
            segment_id: segment_id.into(),
            cancelled: cancelled.clone(),
        });
        state.revisions.insert(
            segment_id.into(),
            SegmentVersion {
                revision,
                source_id,
            },
        );
        Ok(Admission {
            request,
            cancelled,
            chunk,
            owner: Arc::downgrade(&self.0),
            deadline: std::time::Instant::now() + std::time::Duration::from_millis(deadline_ms),
            source_id,
        })
    }

    /// Always call after exchange cleanup, including failure. An old finisher
    /// cannot release another request's slot or mutate another generation.
    pub fn finish(
        &self,
        admitted: &Admission,
        reply: Option<&WorkerReply>,
    ) -> Result<Vec<SourceEvent>, &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.active.as_ref().map(|active| &active.request_id)
            != Some(&admitted.request.request_id)
        {
            return Err("not_admitted");
        }
        state.active = None;
        if admitted.cancelled.load(Ordering::Acquire) {
            return Err("cancelled");
        }
        if std::time::Instant::now() >= admitted.deadline {
            return Err("deadline_expired");
        }
        let reply = reply.ok_or("worker_failed")?;
        if state.session_id != admitted.request.session_id {
            return Err("stale_session");
        }
        let revision = state
            .revisions
            .get(&admitted.request.segment_id)
            .ok_or("stale_segment")?
            .revision;
        validate_reply(&admitted.request, reply, state.generation, revision)
            .map_err(|_| "stale_or_invalid_reply")?;
        if reply.status != WorkerStatus::Ok {
            return Err("worker_failed");
        }
        let result: AsrResult =
            serde_json::from_str(reply.result.as_deref().ok_or("missing_result")?)
                .map_err(|_| "invalid_result")?;
        let samples = admitted.chunk.end_sample - admitted.chunk.start_sample;
        if result.sample_count != samples
            || result.sample_rate != SAMPLE_RATE
            || result.segments.len() > 256
        {
            return Err("invalid_result");
        }
        let duration = samples as f64 / SAMPLE_RATE as f64;
        let mut events = Vec::new();
        for (index, segment) in result.segments.into_iter().enumerate() {
            if !segment.start_seconds.is_finite()
                || !segment.end_seconds.is_finite()
                // Match the existing Whisper decoder's small padded-timestamp
                // tolerance, then Rust bounds the event to the admitted PCM.
                || segment.start_seconds < -0.25
                || segment.end_seconds < segment.start_seconds
                || segment.end_seconds > duration + 0.25
                || segment.text.trim().is_empty()
                || segment.text.chars().count() > 20_000
            {
                return Err("invalid_result");
            }
            let base_ms = admitted.chunk.start_sample * 1000 / SAMPLE_RATE as u64;
            let limit_ms = admitted.chunk.end_sample * 1000 / SAMPLE_RATE as u64;
            events.push(SourceEvent {
                session_id: state.session_id.clone(),
                session_generation: state.generation,
                segment_id: format!("rs-{}-{index}", admitted.source_id),
                source_revision: revision,
                source_language: admitted.request.source_language.clone(),
                stage: "stable",
                text: segment.text,
                start_ms: limit_ms.min(base_ms + (segment.start_seconds * 1000.0).round() as u64),
                end_ms: limit_ms.min(base_ms + (segment.end_seconds * 1000.0).round() as u64),
                timestamp_basis: if segment.estimated_timestamps
                    || segment.start_seconds < 0.0
                    || segment.end_seconds > duration
                {
                    "sample_clock_estimated"
                } else {
                    "sample_clock_native_hint"
                },
            });
        }
        let prepared = state.projection.prepare(
            &state.session_id,
            state.generation,
            &admitted.chunk,
            &events,
        )?;
        // Parsing/projection may itself consume the remaining budget. This is
        // the linearization point; no result is stored after cancel/expiry.
        if admitted.cancelled.load(Ordering::Acquire) {
            return Err("cancelled");
        }
        if std::time::Instant::now() >= admitted.deadline {
            return Err("deadline_expired");
        }
        state
            .accepted
            .insert(admitted.request.segment_id.clone(), events.clone());
        state.projection.apply(prepared);
        Ok(events)
    }
}
