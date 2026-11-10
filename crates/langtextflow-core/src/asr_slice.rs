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
use crate::telemetry::{JobOutcome, QueueTicket};
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
    auto_scope: Option<AutoScope>,
    synthetic_source: bool,
}

#[derive(Default)]
struct AutoScope {
    auto_provenance: bool,
    epoch: u64,
    provider: Option<&'static str>,
    selected_provider: Option<&'static str>,
    generation: u64,
    failovers: u64,
    reason: Option<crate::auto_asr::FailoverReason>,
    rate: u32,
    origin: u64,
    replay: u64,
    active_window: bool,
}

pub(crate) struct AutoSource {
    pub mapped: crate::auto_asr::MappedText,
    pub timestamp_basis: &'static str,
    pub chunk_start: Option<u64>,
    pub chunk_end: u64,
}

struct AcceptedChunk {
    events: Vec<SourceEvent>,
    committed: bool,
}

enum SourceProjection {
    Whisper(AudioChunk),
    Auto(Vec<crate::caption_events::AsrProvenance>),
    Synthetic,
}

struct SourceCompletion {
    chunk_id: String,
    revision: u64,
    events: Vec<SourceEvent>,
    projection: SourceProjection,
    deadline: std::time::Instant,
    cancelled: Option<Arc<AtomicBool>>,
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
    pub(crate) metrics: Option<QueueTicket>,
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
    pub(crate) metrics: Option<QueueTicket>,
}

impl CorrectionJob {
    pub(crate) fn start_metrics(&mut self) {
        if let Some(metrics) = &mut self.metrics {
            metrics.start();
        }
    }
    pub(crate) fn finish_metrics(&mut self, outcome: JobOutcome) {
        let outcome = if self.cancelled.load(Ordering::Acquire) {
            JobOutcome::Cancelled
        } else if std::time::Instant::now() >= self.deadline {
            JobOutcome::Expired
        } else {
            outcome
        };
        if let Some(metrics) = &mut self.metrics {
            metrics.finish(outcome);
        }
    }
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
        if let Some(metrics) = &mut self.metrics {
            metrics.finish(if self.cancelled.load(Ordering::Acquire) {
                JobOutcome::Cancelled
            } else if std::time::Instant::now() >= self.deadline {
                JobOutcome::Expired
            } else if decision.provenance.method == "fallback" {
                JobOutcome::Failed
            } else {
                JobOutcome::Succeeded
            });
        }
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
        self.finish_metrics(JobOutcome::Cancelled);
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
            match config.sender.try_reserve() {
                Ok(permit) => {
                    if let Ok(payload) = serde_json::to_string(&serde_json::json!({
                        "text": job.source, "context": config.context,
                    })) {
                        job.request.payload = payload;
                        job.metrics = Some(config.metrics.admit());
                        permit.send(job);
                    }
                }
                Err(error) => config.metrics.reject(matches!(
                    error,
                    tokio::sync::mpsc::error::TrySendError::Full(_)
                )),
            }
        }
    }
}

impl MtJob {
    pub(crate) fn start_metrics(&mut self) {
        if let Some(metrics) = &mut self.metrics {
            metrics.start();
        }
    }

    pub(crate) fn finish_metrics(&mut self, outcome: JobOutcome) {
        let outcome = if self.cancelled() {
            JobOutcome::Cancelled
        } else if std::time::Instant::now() >= self.deadline {
            JobOutcome::Expired
        } else {
            outcome
        };
        if let Some(metrics) = &mut self.metrics {
            metrics.finish(outcome);
        }
    }
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
            auto_scope: None,
            synthetic_source: false,
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
            metrics: dispatcher.metrics.clone(),
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

    pub(crate) fn start_observed_with_limit(
        &self,
        session_id: &str,
        source: &str,
        targets: &[String],
        max_segments: usize,
    ) -> Result<ObserverCredential, &'static str> {
        if !(1..=10_000).contains(&max_segments) {
            return Err("invalid_live_capacity");
        }
        let (mut config, token) = ObservedConfig::new(source, targets)?;
        config.max_segments = max_segments;
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
        state.auto_scope = None;
        state.synthetic_source = false;
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
        if let Some(scope) = &mut state.auto_scope {
            scope.provider = None;
            scope.active_window = false;
        }
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
            metrics: dispatcher.metrics.clone(),
        });
        state
            .projection
            .config
            .as_mut()
            .unwrap()
            .translation_identity = Some(dispatcher.identity.clone());
        Ok(())
    }

    pub fn observer_metrics(&self) -> ObserverMetrics {
        self.0.lock().unwrap().projection.metrics.clone()
    }

    pub(crate) fn source_metrics(&self) -> crate::telemetry::SourceMetrics {
        let state = self.0.lock().unwrap();
        crate::telemetry::SourceMetrics {
            active_window: state.active.is_some()
                || state.auto_scope.as_ref().is_some_and(|s| s.active_window),
            pending_corrections: state.pending_corrections.len(),
            pending_targets: state.pending_targets.len(),
            active_provider: if state.synthetic_source && !state.session_id.is_empty() {
                Some("mock")
            } else {
                state.auto_scope.as_ref().and_then(|s| s.provider)
            },
            auto_generation: state
                .auto_scope
                .as_ref()
                .filter(|s| s.auto_provenance)
                .map(|s| s.generation),
            failover_count: state
                .auto_scope
                .as_ref()
                .filter(|s| s.auto_provenance)
                .map(|s| s.failovers),
            last_failover_reason: state.auto_scope.as_ref().and_then(|s| s.reason),
        }
    }

    pub(crate) fn enable_metrics(&self) {
        self.0.lock().unwrap().projection.telemetry = Some(tokio::sync::watch::channel(None).0);
    }

    pub(crate) fn publish_metrics(
        &self,
        sid: &str,
        generation: u64,
        frame: crate::caption_events::JsonFrame,
    ) -> bool {
        let state = self.0.lock().unwrap();
        if state.session_id != sid || state.generation != generation {
            return false;
        }
        let Some(sender) = &state.projection.telemetry else {
            return false;
        };
        sender.send_replace(Some(frame));
        true
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

    pub(crate) fn auto_source_scope(
        &self,
        source: &str,
    ) -> Result<(String, u64, Arc<SourceContext>), &'static str> {
        let state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state
                .projection
                .config
                .as_ref()
                .is_none_or(|c| c.source_language != source)
        {
            return Err("auto_source_scope");
        }
        Ok((
            state.session_id.clone(),
            state.generation,
            state.context.clone().ok_or("auto_source_context")?,
        ))
    }

    // Retire before awaiting the outgoing child. Provider epochs never reset
    // caption/session generations or cancel already accepted correction/MT.
    pub(crate) fn begin_auto_candidate(
        &self,
        generation: u64,
        epoch: u64,
    ) -> Result<(), &'static str> {
        self.begin_stream_candidate(generation, epoch, true)
    }

    pub(crate) fn begin_stream_candidate(
        &self,
        generation: u64,
        epoch: u64,
        auto_provenance: bool,
    ) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state.generation != generation
            || epoch == 0
            || epoch > (1 << 53) - 1
            || state.active.is_some()
            || state.auto_scope.as_ref().is_some_and(|s| epoch <= s.epoch)
            || state
                .auto_scope
                .as_ref()
                .is_some_and(|s| s.auto_provenance != auto_provenance)
            || (state.auto_scope.is_none() && !state.revisions.is_empty())
        {
            return Err("stale_auto_candidate");
        }
        let scope = state.auto_scope.get_or_insert_with(Default::default);
        scope.auto_provenance = auto_provenance;
        scope.epoch = epoch;
        scope.provider = None;
        scope.active_window = false;
        Ok(())
    }

    pub(crate) fn activate_auto_candidate(
        &self,
        generation: u64,
        epoch: u64,
        provider: &'static str,
        clock: &crate::auto_asr::AutoClock,
        reason: Option<crate::auto_asr::FailoverReason>,
    ) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state.generation != generation
            || !matches!(provider, "vibevoice" | "faster-whisper")
            || !(8000..=192000).contains(&clock.sample_rate)
            || (provider == "faster-whisper" && clock.sample_rate != 16000)
        {
            return Err("auto_source_provider");
        }
        let scope = state
            .auto_scope
            .as_mut()
            .filter(|s| s.epoch == epoch)
            .ok_or("stale_auto_candidate")?;
        let selection_valid = match scope.selected_provider {
            None => {
                clock.generation == 0
                    && clock.origin_samples == 0
                    && clock.replay_prefix_samples == 0
            }
            Some("vibevoice") => {
                provider == "faster-whisper" && clock.generation == 1 && reason.is_some()
            }
            _ => false,
        };
        if !selection_valid
            || scope.provider.is_some()
            || (scope.rate != 0 && scope.rate != clock.sample_rate)
            || clock.generation > 1
            || clock.generation < scope.generation
        {
            return Err("auto_source_clock");
        }
        if clock.generation > scope.generation {
            scope.failovers += 1;
            scope.reason = reason;
        }
        scope.provider = Some(provider);
        scope.selected_provider = Some(provider);
        scope.generation = clock.generation;
        scope.rate = clock.sample_rate;
        scope.origin = clock.origin_samples;
        scope.replay = clock.replay_prefix_samples;
        if let Some(history) = &state.projection.history {
            history.engine_changed(provider);
        }
        Ok(())
    }

    pub(crate) fn auto_window(
        &self,
        generation: u64,
        epoch: u64,
        active: bool,
    ) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty() || state.generation != generation {
            return Err("stale_auto_candidate");
        }
        let scope = state
            .auto_scope
            .as_mut()
            .filter(|s| s.epoch == epoch)
            .ok_or("stale_auto_candidate")?;
        scope.active_window = active;
        Ok(())
    }

    pub(crate) fn auto_unavailable(&self, generation: u64, epoch: u64) {
        let mut state = self.0.lock().unwrap();
        if state.generation == generation {
            if let Some(scope) = state.auto_scope.as_mut().filter(|s| s.epoch == epoch) {
                scope.provider = None;
                scope.active_window = false;
            }
        }
    }

    pub(crate) fn accept_auto_sources(
        &self,
        generation: u64,
        epoch: u64,
        id: &str,
        sources: Vec<AutoSource>,
        deadline: std::time::Instant,
    ) -> Result<Vec<SourceEvent>, &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state.generation != generation
            || id.is_empty()
            || id.len() > 128
            || sources.len() > 256
            || std::time::Instant::now() >= deadline
        {
            return Err("stale_auto_source");
        }
        let scope = state
            .auto_scope
            .as_ref()
            .filter(|s| s.epoch == epoch)
            .ok_or("stale_auto_candidate")?;
        let provider = scope.provider.ok_or("auto_provider_unavailable")?;
        let model = if provider == "faster-whisper" {
            state
                .projection
                .config
                .as_ref()
                .and_then(|c| c.configured_asr_model.clone())
        } else {
            None
        };
        if state.revisions.contains_key(id) || state.accepted.contains_key(id) {
            return Err("stale_revision");
        }
        if state.revisions.len() >= crate::caption_events::MAX_PUBLIC_IDS {
            return Err("segment_capacity");
        }
        let source_id = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
        let mut events = Vec::new();
        let mut origins = Vec::new();
        for (index, source) in sources.into_iter().enumerate() {
            // Integer-millisecond provider bounds plus a fractional replay
            // origin may differ by one millisecond after round-to-even.
            let limit = ((scope.origin as f64 + source.chunk_end as f64) * 1000.
                / scope.rate as f64)
                .round_ties_even() as u64;
            if source.chunk_end == 0
                || source.chunk_end > (1 << 53) / 1000
                || source
                    .chunk_start
                    .is_some_and(|start| start >= source.chunk_end)
                || source.mapped.text.trim().is_empty()
                || source.mapped.text.chars().count() > 20000
                || source.mapped.start_ms > source.mapped.end_ms
                || source.mapped.end_ms > limit + 1
                || (provider == "vibevoice"
                    && (source.timestamp_basis != "sequence_estimated"
                        || source.chunk_start.is_some()))
                || (provider == "faster-whisper"
                    && (!matches!(
                        source.timestamp_basis,
                        "sample_clock_native_hint" | "sample_clock_estimated"
                    ) || source.chunk_start.is_none()))
            {
                return Err("invalid_auto_source");
            }
            origins.push(crate::caption_events::AsrProvenance {
                provider,
                configured_model: model.clone(),
                verified_model: None,
                model_revision: None,
                timestamp_basis: source.timestamp_basis,
                sample_rate: Some(scope.rate),
                audio_chunk_start_sample: source.chunk_start,
                audio_chunk_end_sample: Some(source.chunk_end),
                auto_generation: scope.auto_provenance.then_some(scope.generation),
                timeline_offset_samples: scope.auto_provenance.then_some(scope.origin),
                replay_prefix_samples: scope.auto_provenance.then_some(scope.replay),
                overlap_trimmed: source.mapped.overlap_trimmed,
            });
            events.push(SourceEvent {
                session_id: state.session_id.clone(),
                session_generation: generation,
                segment_id: format!("rs-{source_id}-{index}"),
                source_revision: 1,
                source_language: state
                    .projection
                    .config
                    .as_ref()
                    .ok_or("observed_session_required")?
                    .source_language
                    .clone(),
                stage: "stable",
                text: source.mapped.text,
                start_ms: source.mapped.start_ms,
                end_ms: source.mapped.end_ms,
                timestamp_basis: source.timestamp_basis,
            });
        }
        state.serial = source_id;
        state.revisions.insert(
            id.into(),
            SegmentVersion {
                revision: 1,
                source_id,
            },
        );
        self.publish_sources(
            state,
            SourceCompletion {
                chunk_id: id.into(),
                revision: 1,
                events,
                projection: SourceProjection::Auto(origins),
                deadline,
                cancelled: None,
            },
        )
    }

    pub(crate) fn enable_mock(&self, generation: u64) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state.generation != generation
            || state.projection.config.is_none()
            || !state.revisions.is_empty()
            || state.auto_scope.is_some()
            || state.active.is_some()
            || state.synthetic_source
        {
            return Err("mock_source_scope");
        }
        state.synthetic_source = true;
        if let Some(history) = &state.projection.history {
            history.engine_changed("mock");
        }
        Ok(())
    }

    pub(crate) fn mock_source(
        &self,
        generation: u64,
        index: u64,
        text: &str,
        stable: bool,
    ) -> Result<(), &'static str> {
        let mut state = self.0.lock().unwrap();
        if state.session_id.is_empty()
            || state.generation != generation
            || !state.synthetic_source
            || index >= crate::caption_events::MAX_PUBLIC_IDS as u64
        {
            return Err("stale_mock_source");
        }
        let id = format!("demo-{:03}-{:02}", index / 3, index % 3);
        let source = SourceEvent {
            session_id: state.session_id.clone(),
            session_generation: generation,
            segment_id: id.clone(),
            source_revision: 1,
            source_language: state
                .projection
                .config
                .as_ref()
                .ok_or("mock_source_scope")?
                .source_language
                .clone(),
            stage: if stable { "stable" } else { "partial" },
            text: text.into(),
            start_ms: index * 6000,
            end_ms: index * 6000 + 5000,
            timestamp_basis: "synthetic",
        };
        if !stable {
            let frames =
                state
                    .projection
                    .prepare_mock_partial(&state.session_id, generation, &source)?;
            state.projection.apply(frames)?;
            return Ok(());
        }
        // The timer owns one immutable partial→stable revision. It cannot
        // rewrite an existing stable source or accept a retired session timer.
        if state.revisions.contains_key(&id)
            || state.accepted.contains_key(&id)
            || !state.projection.mock_partial_ready(&id)
        {
            return Err("stale_revision");
        }
        let source_id = state.serial.checked_add(1).ok_or("request_id_exhausted")?;
        state.serial = source_id;
        state.revisions.insert(
            id.clone(),
            SegmentVersion {
                revision: 1,
                source_id,
            },
        );
        self.publish_sources(
            state,
            SourceCompletion {
                chunk_id: id,
                revision: 1,
                events: vec![source],
                projection: SourceProjection::Synthetic,
                deadline: std::time::Instant::now() + std::time::Duration::from_secs(120),
                cancelled: None,
            },
        )?;
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
            || !crate::supported_language(source)
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
            || state.synthetic_source
            || !state.revisions.is_empty()
            || state.active.is_some()
            || !crate::supported_language(source)
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
            || !crate::supported_language(source_language)
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
        if state.auto_scope.is_some() {
            return Err("auto_source_owner_required");
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
        self.publish_sources(
            state,
            SourceCompletion {
                chunk_id: admitted.request.segment_id.clone(),
                revision,
                events,
                projection: SourceProjection::Whisper(admitted.chunk.clone()),
                deadline: admitted.deadline,
                cancelled: Some(admitted.cancelled.clone()),
            },
        )
    }

    fn publish_sources(
        &self,
        mut state: std::sync::MutexGuard<'_, State>,
        batch: SourceCompletion,
    ) -> Result<Vec<SourceEvent>, &'static str> {
        let SourceCompletion {
            chunk_id,
            revision,
            events,
            projection,
            deadline,
            cancelled,
        } = batch;
        let prepared = match &projection {
            SourceProjection::Whisper(chunk) => state.projection.prepare(
                &state.session_id,
                state.generation,
                chunk,
                &events,
                state.translation.is_some(),
                state.correction.is_some(),
            )?,
            SourceProjection::Auto(origins) => state.projection.prepare_asr(
                &state.session_id,
                state.generation,
                &events,
                origins,
                state.translation.is_some(),
                state.correction.is_some(),
            )?,
            SourceProjection::Synthetic => state.projection.prepare_mock_stable(
                &state.session_id,
                state.generation,
                &events,
                state.translation.is_some(),
                state.correction.is_some(),
            )?,
        };
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
                        metrics: None,
                    });
                }
            }
        }
        // Parsing/projection may itself consume the remaining budget. This is
        // the linearization point; no result is stored after cancel/expiry.
        if cancelled
            .as_ref()
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            return Err("cancelled");
        }
        if std::time::Instant::now() >= deadline {
            return Err("deadline_expired");
        }
        state.accepted.insert(
            chunk_id.clone(),
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
                    chunk_id: chunk_id.clone(),
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
                        chunk_id: chunk_id.clone(),
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
                    metrics: None,
                });
            }
        }
        state.projection.apply(prepared)?;
        drop(state);
        if let Some(config) = correction {
            for mut job in corrections {
                job.armed = true;
                match config.sender.try_reserve() {
                    Ok(permit) => {
                        job.metrics = Some(config.metrics.admit());
                        permit.send(job);
                    }
                    Err(error) => config.metrics.reject(matches!(
                        error,
                        tokio::sync::mpsc::error::TrySendError::Full(_)
                    )),
                }
            }
        } else {
            submit_translation(jobs, translation.as_ref());
        }
        Ok(events)
    }
}

#[cfg(test)]
mod auto_authority_tests {
    use super::*;
    use crate::auto_asr::{AutoClock, FailoverReason, MappedText};

    fn setup(rate: u32) -> (AsrSession, AutoClock, String, u64) {
        let session = AsrSession::default();
        let credential = session
            .start_observed("auto-session", "en", &["ko".into(), "ja".into()])
            .unwrap();
        session
            .configure_context("en", SourceContext::default())
            .unwrap();
        session.configured_asr_model(Some("small".into())).unwrap();
        let generation = session.audio_generation("en").unwrap();
        session.begin_auto_candidate(generation, 1).unwrap();
        let mut clock = AutoClock::default();
        clock.candidate(rate, 0, 0, 0).unwrap();
        session
            .activate_auto_candidate(generation, 1, "vibevoice", &clock, None)
            .unwrap();
        (
            session,
            clock,
            credential.path().split("token=").nth(1).unwrap().into(),
            generation,
        )
    }

    fn source(text: &str, start: u64, end: u64, rate: u64) -> AutoSource {
        AutoSource {
            mapped: MappedText {
                text: text.into(),
                start_ms: start,
                end_ms: end,
                overlap_trimmed: false,
            },
            timestamp_basis: "sequence_estimated",
            chunk_start: None,
            chunk_end: end * rate / 1000,
        }
    }

    fn snapshot(session: &AsrSession, token: &str) -> serde_json::Value {
        let mut observer = session.subscribe_observer(token).unwrap();
        serde_json::from_str(observer.receiver.frames.try_recv().unwrap().get()).unwrap()
    }

    #[test]
    fn sidecar_clock_model_privacy_and_retired_candidate_or_session_are_authoritative() {
        let (session, _clock, token, generation) = setup(48000);
        session
            .accept_auto_sources(
                generation,
                1,
                "first",
                vec![source("Original source", 0, 25, 48000)],
                std::time::Instant::now() + std::time::Duration::from_secs(2),
            )
            .unwrap();
        let frame = snapshot(&session, &token);
        let event = &frame["segments"][0];
        assert_eq!(event["original_text"], "Original source");
        assert_eq!(event["asr_provenance"]["provider"], "vibevoice");
        assert_eq!(event["asr_provenance"]["sample_rate"], 48000);
        assert_eq!(
            event["asr_provenance"]["timestamp_basis"],
            "sequence_estimated"
        );
        assert!(event["asr_provenance"]["configured_model"].is_null());
        assert!(event["asr_provenance"]["audio_chunk_start_sample"].is_null());
        assert_eq!(event["asr_provenance"]["audio_chunk_end_sample"], 1200);
        session.begin_auto_candidate(generation, 2).unwrap();
        assert!(session
            .accept_auto_sources(
                generation,
                1,
                "late",
                vec![source("Late source", 25, 50, 48000)],
                std::time::Instant::now() + std::time::Duration::from_secs(2)
            )
            .is_err());
        let mut incompatible = AutoClock::default();
        incompatible.candidate(16000, 1, 0, 0).unwrap();
        assert!(session
            .activate_auto_candidate(
                generation,
                2,
                "faster-whisper",
                &incompatible,
                Some(FailoverReason::ProviderFailed)
            )
            .is_err());
        session
            .start_observed("new-session", "en", &["ko".into()])
            .unwrap();
        assert!(session
            .accept_auto_sources(
                generation,
                2,
                "late-session",
                vec![],
                std::time::Instant::now() + std::time::Duration::from_secs(2)
            )
            .is_err());
        assert!(!session.authorize_observer(&token));
    }

    #[test]
    fn failover_preserves_originals_native_hint_and_distinct_provider_local_replay_scope() {
        let (session, mut clock, token, generation) = setup(16000);
        session
            .accept_auto_sources(
                generation,
                1,
                "old",
                vec![source("First source", 0, 1000, 16000)],
                std::time::Instant::now() + std::time::Duration::from_secs(2),
            )
            .unwrap();
        session.begin_auto_candidate(generation, 2).unwrap();
        clock.candidate(16000, 1, 8000, 8000).unwrap();
        session
            .activate_auto_candidate(
                generation,
                2,
                "faster-whisper",
                &clock,
                Some(FailoverReason::ProviderClosed),
            )
            .unwrap();
        session
            .accept_auto_sources(
                generation,
                2,
                "new",
                vec![AutoSource {
                    mapped: MappedText {
                        text: "Next source".into(),
                        start_ms: 1000,
                        end_ms: 1500,
                        overlap_trimmed: true,
                    },
                    timestamp_basis: "sample_clock_native_hint",
                    chunk_start: Some(0),
                    chunk_end: 16000,
                }],
                std::time::Instant::now() + std::time::Duration::from_secs(2),
            )
            .unwrap();
        let frame = snapshot(&session, &token);
        assert_eq!(frame["segments"][0]["original_text"], "First source");
        let provenance = &frame["segments"][1]["asr_provenance"];
        assert_eq!(provenance["provider"], "faster-whisper");
        assert_eq!(provenance["configured_model"], "small");
        assert!(provenance["verified_model"].is_null() && provenance["model_revision"].is_null());
        assert_eq!(provenance["auto_generation"], 1);
        assert_eq!(provenance["timeline_offset_samples"], 8000);
        assert_eq!(provenance["replay_prefix_samples"], 8000);
        assert_eq!(provenance["audio_chunk_start_sample"], 0);
        assert_eq!(provenance["timestamp_basis"], "sample_clock_native_hint");
        assert_eq!(provenance["overlap_trimmed"], true);
        assert_eq!(session.source_metrics().failover_count, Some(1));
    }

    #[test]
    fn provider_selection_is_one_way_and_initial_generation_cannot_claim_replay() {
        let (session, mut clock, _, generation) = setup(16000);
        assert!(session
            .activate_auto_candidate(generation, 1, "vibevoice", &clock, None)
            .is_err());
        session.begin_auto_candidate(generation, 2).unwrap();
        assert!(session
            .activate_auto_candidate(generation, 2, "vibevoice", &clock, None)
            .is_err());
        clock.candidate(16000, 1, 0, 0).unwrap();
        session
            .activate_auto_candidate(
                generation,
                2,
                "faster-whisper",
                &clock,
                Some(FailoverReason::ProviderFailed),
            )
            .unwrap();
        session.begin_auto_candidate(generation, 3).unwrap();
        assert!(session
            .activate_auto_candidate(generation, 3, "vibevoice", &clock, None)
            .is_err());
        assert!(session
            .activate_auto_candidate(
                generation,
                3,
                "faster-whisper",
                &clock,
                Some(FailoverReason::ProviderFailed)
            )
            .is_err());
        let fresh = AsrSession::default();
        fresh.start_observed("fresh", "en", &["ko".into()]).unwrap();
        fresh
            .configure_context("en", SourceContext::default())
            .unwrap();
        let generation = fresh.audio_generation("en").unwrap();
        fresh.begin_auto_candidate(generation, 1).unwrap();
        assert!(fresh
            .activate_auto_candidate(generation, 1, "faster-whisper", &clock, None)
            .is_err());
    }

    #[test]
    fn short_sidecar_sources_can_cross_whisper_window_capacity_without_payload_retention() {
        let (session, _clock, token, generation) = setup(48000);
        for index in 0..MAX_SOURCE_CHUNKS + 2 {
            session
                .accept_auto_sources(
                    generation,
                    1,
                    &format!("sidecar-{index}"),
                    vec![source(
                        "source",
                        index as u64 * 2000,
                        (index as u64 + 1) * 2000,
                        48000,
                    )],
                    std::time::Instant::now() + std::time::Duration::from_secs(2),
                )
                .unwrap();
        }
        let state = session.0.lock().unwrap();
        assert_eq!(state.revisions.len(), MAX_SOURCE_CHUNKS + 2);
        assert!(state
            .accepted
            .values()
            .all(|c| c.committed && c.events.capacity() == 0));
        drop(state);
        assert_eq!(
            snapshot(&session, &token)["segments"]
                .as_array()
                .unwrap()
                .len(),
            100
        );
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
