//! First ASR vertical slice: Rust admission and immutable inference-result acceptance.
//! It is opt-in and does not replace the production Python caption runtime.
use crate::caption_events::{
    CaptionProjection, ObservedConfig, ObserverCredential, ObserverMetrics, ObserverReceiver,
};
use crate::correction_slice::{
    CorrectionAdmission, CorrectionDecision, CorrectionDispatcher, CorrectionOptions,
};
use crate::mt_slice::{MtAdmission, MtDispatcher, MtOutcome, TranslationContext};
use crate::source_context::SourceContext;
use crate::{validate_reply, TaskKind, WorkerReply, WorkerRequest, WorkerStatus, PROTOCOL_VERSION};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex, Weak,
};

const SAMPLE_RATE: u32 = 16_000;
const MAX_CHUNK_SAMPLES: u64 = 120 * SAMPLE_RATE as u64;
pub(crate) const MAX_SOURCE_CHUNKS: usize = 2048;

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

pub(crate) struct AudioBinding {
    pub generation: u64,
    pub deadline: std::time::Instant,
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
    accepted: HashMap<String, AcceptedChunk>,
    projection: CaptionProjection,
    translation: Option<MtAdmission>,
    pending_targets: HashMap<String, PendingTarget>,
    context: Option<Arc<SourceContext>>,
    correction: Option<CorrectionAdmission>,
    pending_corrections: HashMap<String, PendingTarget>,
}

struct AcceptedChunk {
    events: Vec<SourceEvent>,
    committed: bool,
}

struct PendingTarget {
    chunk_id: String,
    public_id: String,
    cancelled: Arc<AtomicBool>,
}

pub(crate) struct MtJob {
    pub request: WorkerRequest,
    pub cancelled: Arc<AtomicBool>,
    pub deadline: std::time::Instant,
    pub source: String,
    owner: Weak<Mutex<State>>,
    armed: bool,
}

pub(crate) struct CorrectionJob {
    pub request: WorkerRequest,
    pub source: String,
    pub context: Arc<SourceContext>,
    pub deadline: std::time::Instant,
    pub cancelled: Arc<AtomicBool>,
    options: Arc<CorrectionOptions>,
    translation: Option<Arc<MtAdmission>>,
    jobs: Vec<MtJob>,
    owner: Weak<Mutex<State>>,
    armed: bool,
}

impl CorrectionJob {
    pub(crate) fn record_progress(&self, deterministic: &str, attempted: bool) -> bool {
        let Some(owner) = self.owner.upgrade() else {
            return false;
        };
        let mut state = owner.lock().unwrap();
        if self.cancelled.load(Ordering::Acquire)
            || std::time::Instant::now() >= self.deadline
            || state.session_id != self.request.session_id
            || state.generation != self.request.session_generation
            || !state
                .pending_corrections
                .contains_key(&self.request.request_id)
        {
            return false;
        }
        state.projection.correction_progress(
            &self.request.segment_id,
            self.request.source_revision,
            deterministic,
            attempted,
        )
    }

    pub(crate) fn finish(&mut self, decision: CorrectionDecision) {
        self.armed = false;
        let Some(owner) = self.owner.upgrade() else {
            return;
        };
        let mut state = owner.lock().unwrap();
        let Some(pending) = state.pending_corrections.remove(&self.request.request_id) else {
            return;
        };
        if self.cancelled.load(Ordering::Acquire)
            || state.session_id != self.request.session_id
            || state.generation != self.request.session_generation
            || state
                .revisions
                .get(&pending.chunk_id)
                .map(|version| version.revision)
                != Some(self.request.source_revision)
        {
            return;
        }
        let text = state
            .projection
            .corrected(
                &self.request.segment_id,
                self.request.source_revision,
                decision,
                &self.options,
                self.deadline,
                &self.cancelled,
                self.translation.is_some(),
            )
            .ok()
            .flatten();
        if self.translation.is_none() {
            if let Some(accepted) = state.accepted.get_mut(&pending.chunk_id) {
                accepted.committed = true;
                accepted.events.clear();
            }
        }
        drop(state);
        if let Some(text) = text {
            let mut jobs = std::mem::take(&mut self.jobs);
            for job in &mut jobs {
                job.source = text.clone();
            }
            submit_translation(jobs, self.translation.as_deref());
        } else {
            // Even a projection failure cannot leave already-admitted targets pending.
            for job in &mut self.jobs {
                job.armed = true;
            }
            self.jobs.clear();
        }
    }
}

impl Drop for CorrectionJob {
    fn drop(&mut self) {
        if self.armed {
            self.finish(CorrectionDecision::fallback(
                &self.source,
                &self.source,
                &self.options,
                false,
                "correction provider unavailable",
            ));
        }
    }
}

fn submit_translation(jobs: Vec<MtJob>, config: Option<&MtAdmission>) {
    if let Some(config) = config {
        for mut job in jobs {
            job.armed = true;
            if let Ok(permit) = config.sender.try_reserve() {
                if let Ok(payload) = serde_json::to_string(&serde_json::json!({
                    "text": job.source, "context": config.context,
                })) {
                    job.request.payload = payload;
                    permit.send(job);
                }
            }
        }
    }
}

impl MtJob {
    pub(crate) fn cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    pub(crate) fn finish(&mut self, outcome: MtOutcome) {
        self.armed = false;
        let Some(owner) = self.owner.upgrade() else {
            return;
        };
        let mut state = owner.lock().unwrap();
        let Some(pending) = state.pending_targets.remove(&self.request.request_id) else {
            return;
        };
        if state.session_id != self.request.session_id
            || state.generation != self.request.session_generation
            || state
                .revisions
                .get(&pending.chunk_id)
                .map(|value| value.revision)
                != Some(self.request.source_revision)
        {
            return;
        }
        let outcome = if std::time::Instant::now() >= self.deadline {
            MtOutcome::TimedOut
        } else if self.cancelled() {
            MtOutcome::Failed
        } else {
            outcome
        };
        let _ = state.projection.target(
            &self.request.segment_id,
            self.request.source_revision,
            self.request.target_language.as_deref().unwrap(),
            outcome,
            self.deadline,
            &self.cancelled,
        );
        if !state
            .pending_targets
            .values()
            .any(|other| other.public_id == pending.public_id)
        {
            if let Some(accepted) = state.accepted.get_mut(&pending.chunk_id) {
                accepted.committed = true;
                // Terminal chunks cannot be revised. Keep their identity fence,
                // not a second session-long copy of completed source text.
                accepted.events = Vec::new();
            }
        }
    }
}

impl Drop for MtJob {
    fn drop(&mut self) {
        if self.armed {
            self.finish(MtOutcome::Failed);
        }
    }
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
            translation: None,
            pending_targets: HashMap::new(),
            context: None,
            correction: None,
            pending_corrections: HashMap::new(),
        })))
    }
}

impl AsrSession {
    pub fn enable_correction(
        &self,
        dispatcher: &CorrectionDispatcher,
        options: CorrectionOptions,
    ) -> Result<(), &'static str> {
        options.validate()?;
        let mut state = self.0.lock().unwrap();
        if state.context.is_none()
            || !state.revisions.is_empty()
            || state.correction.is_some()
            || dispatcher.sender.is_closed()
            || dispatcher.options != options
        {
            return Err("correction_requires_new_context");
        }
        state.correction = Some(CorrectionAdmission {
            sender: dispatcher.sender.clone(),
            options,
        });
        Ok(())
    }

    pub fn pending_postprocessing(&self) -> usize {
        let state = self.0.lock().unwrap();
        state.pending_targets.len() + state.pending_corrections.len()
    }
    /// Capture once before admission; source revisions cannot change inference inputs.
    pub fn configure_context(
        &self,
        source: &str,
        context: SourceContext,
    ) -> Result<(), &'static str> {
        context.validate()?;
        let mut state = self.0.lock().unwrap();
        if state.context.is_some()
            || !state.revisions.is_empty()
            || !state
                .projection
                .config
                .as_ref()
                .is_some_and(|config| config.source_language == source)
        {
            return Err("source_context_scope");
        }
        state.context = Some(Arc::new(context));
        Ok(())
    }

    /// Opt-in after Python history bootstrap, before the first source admission.
    pub fn attach_history(&self, sink: crate::history::HistorySink) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        let config = state
            .projection
            .config
            .as_ref()
            .ok_or("history_requires_observed_session")?;
        if !state.revisions.is_empty()
            || state.projection.history.is_some()
            || !sink.matches(
                &state.session_id,
                state.generation,
                &config.source_language,
                &config.targets,
            )
        {
            return Err("history_scope");
        }
        state.projection.history = Some(sink);
        Ok(())
    }

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
        let options = state
            .correction
            .as_ref()
            .map(|config| config.options.clone());
        state.projection.cancel_pending(options.as_ref())?;
        if let Some(active) = &state.active {
            active.cancelled.store(true, Ordering::Release);
        }
        state.generation = next_generation;
        for pending in state.pending_targets.drain().map(|(_, value)| value) {
            pending.cancelled.store(true, Ordering::Release);
        }
        state.translation = None;
        state.context = None;
        state.correction = None;
        for pending in state
            .pending_corrections
            .drain()
            .map(|(_, pending)| pending)
        {
            pending.cancelled.store(true, Ordering::Release);
        }
        state.session_id = session_id.into();
        state.revisions.clear();
        state.accepted.clear();
        state.projection.reset(config);
        // Admission is retained until the previous request is finished/reaped.
        Ok(())
    }

    pub fn stop(&self) {
        let _ = self.stop_checked();
    }

    pub fn stop_checked(&self) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        let options = state
            .correction
            .as_ref()
            .map(|config| config.options.clone());
        let finalized = state.projection.cancel_pending(options.as_ref());
        if let Some(active) = &state.active {
            active.cancelled.store(true, Ordering::Release);
        }
        state.session_id.clear();
        state.revisions.clear();
        state.accepted.clear();
        for pending in state.pending_targets.drain().map(|(_, value)| value) {
            pending.cancelled.store(true, Ordering::Release);
        }
        state.translation = None;
        state.context = None;
        state.correction = None;
        for pending in state
            .pending_corrections
            .drain()
            .map(|(_, pending)| pending)
        {
            pending.cancelled.store(true, Ordering::Release);
        }
        state.projection.reset(None);
        finalized
    }

    /// Explicit prototype opt-in, before source admission. The dispatcher owns
    /// its task independently; this authority stores only its bounded sender.
    pub fn enable_translation(
        &self,
        dispatcher: &MtDispatcher,
        context: TranslationContext,
        deadline_ms: u64,
    ) -> Result<(), &'static str> {
        context.validate()?;
        if deadline_ms == 0 || deadline_ms > 120_000 || dispatcher.sender.is_closed() {
            return Err("invalid_translation_deadline_or_owner");
        }
        let mut state = self.0.lock().unwrap();
        if state.projection.config.is_none() || !state.revisions.is_empty() {
            return Err("translation_requires_new_observed_session");
        }
        if state.context.as_ref().is_some_and(|snapshot| {
            context.preset != snapshot.preset || context.glossary != snapshot.glossary
        }) {
            return Err("translation_context_scope");
        }
        state.translation = Some(MtAdmission {
            sender: dispatcher.sender.clone(),
            context,
            deadline_ms,
        });
        Ok(())
    }

    pub fn observer_metrics(&self) -> ObserverMetrics {
        self.0.lock().unwrap().projection.metrics.clone()
    }

    pub fn pending_translation_targets(&self) -> usize {
        self.0.lock().unwrap().pending_targets.len()
    }

    pub(crate) fn publication_serial(&self) -> u64 {
        self.0.lock().unwrap().projection.serial
    }

    pub(crate) fn configured_asr_model(&self, model: Option<String>) -> Result<(), &'static str> {
        if model.as_ref().is_some_and(|model| {
            model.is_empty()
                || model.len() > 200
                || model.starts_with('.')
                || std::path::Path::new(model).is_dir()
                || model.split('/').count() > 2
                || model.split('/').any(|part| part.is_empty() || part == "..")
                || !model
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"-._/".contains(&c))
        }) {
            return Err("private_model_identity");
        }
        let mut state = self.0.lock().unwrap();
        if !state.revisions.is_empty() {
            return Err("model_identity_requires_fresh_session");
        }
        state
            .projection
            .config
            .as_mut()
            .ok_or("observed_session_required")?
            .configured_asr_model = model;
        Ok(())
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
        if state
            .accepted
            .get(segment_id)
            .is_some_and(|chunk| chunk.committed)
        {
            return Err("accepted_chunk");
        }
        let old = state.revisions.get(segment_id).ok_or("unknown_segment")?;
        let old_revision = old.revision;
        if revision <= old.revision || revision > (1_u64 << 53) - 1 {
            return Err("stale_revision");
        }
        let obsolete = state
            .accepted
            .get(segment_id)
            .map(|chunk| {
                chunk
                    .events
                    .iter()
                    .map(|event| event.segment_id.clone())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let (sid, generation) = (state.session_id.clone(), state.generation);
        state
            .projection
            .invalidate(&sid, generation, &obsolete, old_revision)?;
        state.revisions.get_mut(segment_id).unwrap().revision = revision;
        state.accepted.remove(segment_id);
        state.pending_targets.retain(|_, pending| {
            if pending.chunk_id == segment_id {
                pending.cancelled.store(true, Ordering::Release);
                false
            } else {
                true
            }
        });
        state.pending_corrections.retain(|_, pending| {
            if pending.chunk_id == segment_id {
                pending.cancelled.store(true, Ordering::Release);
                false
            } else {
                true
            }
        });
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
        self.admit_bound(
            segment_id,
            revision,
            source_language,
            chunk,
            deadline_ms,
            None,
        )
    }

    pub(crate) fn audio_generation(&self, source: &str) -> Result<u64, &'static str> {
        let state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || !matches!(source, "ko" | "en" | "ja")
            || state
                .projection
                .config
                .as_ref()
                .is_some_and(|config| config.source_language != source)
        {
            return Err("no_matching_audio_session");
        }
        Ok(state.generation)
    }

    pub(crate) fn bind_audio_generation(&self, source: &str) -> Result<u64, &'static str> {
        let state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || !state.revisions.is_empty()
            || state.active.is_some()
            || !matches!(source, "ko" | "en" | "ja")
            || state
                .projection
                .config
                .as_ref()
                .is_some_and(|config| config.source_language != source)
        {
            return Err("audio_requires_fresh_matching_session");
        }
        Ok(state.generation)
    }

    pub(crate) fn admit_window(
        &self,
        id: &str,
        source: &str,
        chunk: AudioChunk,
        binding: &AudioBinding,
    ) -> Result<Admission, &'static str> {
        self.admit_bound(id, 1, source, chunk, 120_000, Some(binding))
    }

    fn admit_bound(
        &self,
        segment_id: &str,
        revision: u64,
        source_language: &str,
        chunk: AudioChunk,
        deadline_ms: u64,
        binding: Option<&AudioBinding>,
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
        if binding.is_some_and(|bound| {
            bound.generation != state.generation || std::time::Instant::now() >= bound.deadline
        }) {
            return Err("stale_audio_or_deadline");
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
        if !state.revisions.contains_key(segment_id) && state.revisions.len() >= MAX_SOURCE_CHUNKS {
            return Err("segment_capacity");
        }
        state.serial = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
        let source_id = state
            .revisions
            .get(segment_id)
            .map_or(state.serial, |old| old.source_id);
        // A private immutable WAV window starts at file offset zero. Caption
        // authority keeps the original continuous PCM interval independently.
        let file_chunk = if binding.is_some() {
            AudioChunk {
                wav_path: chunk.wav_path.clone(),
                start_sample: 0,
                end_sample: chunk.end_sample - chunk.start_sample,
                sample_rate: chunk.sample_rate,
            }
        } else {
            chunk.clone()
        };
        #[derive(Serialize)]
        struct Payload<'a> {
            #[serde(flatten)]
            chunk: &'a AudioChunk,
            #[serde(skip_serializing_if = "Option::is_none")]
            context: Option<&'a SourceContext>,
        }
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
            payload: serde_json::to_string(&Payload {
                chunk: &file_chunk,
                context: state.context.as_deref(),
            })
            .map_err(|_| "invalid_payload")?,
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
            deadline: binding.map_or_else(
                || std::time::Instant::now() + std::time::Duration::from_millis(deadline_ms),
                |bound| bound.deadline,
            ),
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
            state.translation.is_some(),
            state.correction.is_some(),
        )?;
        let translation = state.translation.clone();
        let correction = state.correction.clone();
        let published_at = std::time::Instant::now();
        let mut jobs = Vec::new();
        if let Some(config) = &translation {
            let targets = state.projection.config.as_ref().unwrap().targets.clone();
            let deadline = published_at + std::time::Duration::from_millis(config.deadline_ms);
            for event in &events {
                for target in &targets {
                    state.serial = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
                    let request = WorkerRequest {
                        protocol_version: PROTOCOL_VERSION,
                        request_id: format!("mt{}", state.serial),
                        session_id: state.session_id.clone(),
                        session_generation: state.generation,
                        segment_id: event.segment_id.clone(),
                        source_revision: revision,
                        task_kind: TaskKind::Translation,
                        source_language: event.source_language.clone(),
                        target_language: Some(target.clone()),
                        deadline_ms: config.deadline_ms,
                        // Serialize glossary/source only after obtaining bounded
                        // queue capacity, rather than copying it for rejected jobs.
                        payload: String::new(),
                    };
                    jobs.push(MtJob {
                        request,
                        cancelled: Arc::new(AtomicBool::new(false)),
                        deadline,
                        source: event.text.clone(),
                        owner: Arc::downgrade(&self.0),
                        armed: false,
                    });
                }
            }
        }
        // Parsing/projection may itself consume the remaining budget. This is
        // the linearization point; no result is stored after cancel/expiry.
        if admitted.cancelled.load(Ordering::Acquire) {
            return Err("cancelled");
        }
        if std::time::Instant::now() >= admitted.deadline {
            return Err("deadline_expired");
        }
        state.accepted.insert(
            admitted.request.segment_id.clone(),
            AcceptedChunk {
                events: if (translation.is_none() && correction.is_none()) || events.is_empty() {
                    Vec::new()
                } else {
                    events.clone()
                },
                committed: (translation.is_none() && correction.is_none()) || events.is_empty(),
            },
        );
        for job in &jobs {
            state.pending_targets.insert(
                job.request.request_id.clone(),
                PendingTarget {
                    chunk_id: admitted.request.segment_id.clone(),
                    public_id: job.request.segment_id.clone(),
                    cancelled: job.cancelled.clone(),
                },
            );
        }
        let mut corrections = Vec::new();
        if let Some(config) = &correction {
            let shared_translation = translation.clone().map(Arc::new);
            let shared_options = Arc::new(config.options.clone());
            let mut grouped: HashMap<String, Vec<MtJob>> = HashMap::new();
            for job in jobs.drain(..) {
                grouped
                    .entry(job.request.segment_id.clone())
                    .or_default()
                    .push(job);
            }
            let mut deadline =
                published_at + std::time::Duration::from_secs_f64(config.options.timeout_seconds);
            if let Some(mt) = &translation {
                deadline =
                    deadline.min(published_at + std::time::Duration::from_millis(mt.deadline_ms));
            }
            for event in &events {
                state.serial = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
                let request = WorkerRequest {
                    protocol_version: PROTOCOL_VERSION,
                    request_id: format!("cr{}", state.serial),
                    session_id: state.session_id.clone(),
                    session_generation: state.generation,
                    segment_id: event.segment_id.clone(),
                    source_revision: revision,
                    task_kind: TaskKind::Correction,
                    source_language: event.source_language.clone(),
                    target_language: None,
                    deadline_ms: (config.options.timeout_seconds * 1000.).round() as u64,
                    payload: String::new(),
                };
                let cancelled = Arc::new(AtomicBool::new(false));
                state.pending_corrections.insert(
                    request.request_id.clone(),
                    PendingTarget {
                        chunk_id: admitted.request.segment_id.clone(),
                        public_id: event.segment_id.clone(),
                        cancelled: cancelled.clone(),
                    },
                );
                corrections.push(CorrectionJob {
                    request,
                    source: event.text.clone(),
                    context: state.context.as_ref().unwrap().clone(),
                    deadline,
                    cancelled,
                    options: shared_options.clone(),
                    translation: shared_translation.clone(),
                    jobs: grouped.remove(&event.segment_id).unwrap_or_default(),
                    owner: Arc::downgrade(&self.0),
                    armed: false,
                });
            }
        }
        state.projection.apply(prepared)?;
        drop(state);
        if let Some(config) = correction {
            for mut job in corrections {
                job.armed = true;
                let _ = config.sender.try_send(job);
            }
        } else {
            submit_translation(jobs, translation.as_ref());
        }
        Ok(events)
    }
}

#[cfg(test)]
mod retention_tests {
    use super::*;

    #[test]
    fn completed_chunks_keep_bounded_terminal_tombstones_without_source_payload_copies() {
        let session = AsrSession::default();
        session.start("bounded").unwrap();
        for index in 0..MAX_SOURCE_CHUNKS {
            let admitted = session
                .admit(
                    &format!("chunk-{index}"),
                    1,
                    "ko",
                    AudioChunk {
                        wav_path: "synthetic.wav".into(),
                        start_sample: index as u64 * 16000,
                        end_sample: (index as u64 + 1) * 16000,
                        sample_rate: 16000,
                    },
                    2000,
                )
                .unwrap();
            let request = admitted.request();
            let reply = WorkerReply {
                protocol_version: 1,
                request_id: request.request_id.clone(),
                session_id: request.session_id.clone(),
                session_generation: request.session_generation,
                segment_id: request.segment_id.clone(),
                source_revision: 1,
                status: WorkerStatus::Ok,
                error: None,
                result: Some(
                    serde_json::to_string(&AsrResult {
                        sample_count: 16000,
                        sample_rate: 16000,
                        segments: vec![InferredSegment {
                            start_seconds: 0.0,
                            end_seconds: 1.0,
                            text: "source ".repeat(500),
                            estimated_timestamps: false,
                        }],
                    })
                    .unwrap(),
                ),
            };
            session.finish(&admitted, Some(&reply)).unwrap();
        }
        let state = session.0.lock().unwrap();
        assert_eq!(state.accepted.len(), MAX_SOURCE_CHUNKS);
        assert_eq!(state.revisions.len(), MAX_SOURCE_CHUNKS);
        assert!(state
            .accepted
            .values()
            .all(|chunk| chunk.committed && chunk.events.capacity() == 0));
        drop(state);
        assert_eq!(session.revise("chunk-0", 2).unwrap_err(), "accepted_chunk");
        assert!(matches!(
            session.admit(
                "overflow",
                1,
                "ko",
                AudioChunk {
                    wav_path: "synthetic.wav".into(),
                    start_sample: 0,
                    end_sample: 16000,
                    sample_rate: 16000,
                },
                2000
            ),
            Err("segment_capacity")
        ));
    }
}
