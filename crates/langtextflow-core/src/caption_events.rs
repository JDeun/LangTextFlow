//! Source-only canonical events. No MT, correction, persistence or UI cutover.
use crate::asr_slice::{AudioChunk, SourceEvent};
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
const SNAPSHOT_OVERHEAD: usize = 2048;
const JS_MAX: u64 = (1 << 53) - 1;
pub type JsonFrame = Arc<RawValue>;

pub struct ObservedConfig {
    pub source_language: String,
    pub targets: Vec<String>,
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
                token: token.clone(),
            },
            ObserverCredential(token),
        ))
    }
}

#[derive(Clone, Serialize)]
struct AsrProvenance {
    provider: &'static str,
    configured_model: Option<String>,
    verified_model: Option<String>,
    model_revision: Option<String>,
    timestamp_basis: &'static str,
    sample_rate: u32,
    audio_chunk_start_sample: u64,
    audio_chunk_end_sample: u64,
    auto_generation: Option<u64>,
    timeline_offset_samples: Option<u64>,
    replay_prefix_samples: Option<u64>,
    overlap_trimmed: bool,
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
    original_text: String,
    translations: BTreeMap<String, String>,
    translation_states: BTreeMap<String, &'static str>,
    asr_provenance: AsrProvenance,
    translation_provenance: BTreeMap<String, String>,
    start_ms: u64,
    end_ms: u64,
    speaker: Option<String>,
    confidence: Option<f64>,
    correction: Option<String>,
    committed: bool,
    emitted_at: String,
    session_id: String,
    session_generation: u64,
    serial: u64,
}

#[derive(Clone)]
pub struct ProjectedFrame {
    pub(crate) raw: JsonFrame,
    id: String,
    serial: u64,
    start_ms: u64,
}

pub struct ObserverReceiver {
    pub(crate) id: u64,
    pub(crate) frames: mpsc::Receiver<JsonFrame>,
    pub(crate) control: watch::Receiver<bool>,
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
    pub(crate) config: Option<ObservedConfig>,
    latest: VecDeque<ProjectedFrame>,
    latest_bytes: usize,
    serial: u64,
    next_client: u64,
    subscribers: HashMap<u64, Subscriber>,
    pub(crate) metrics: ObserverMetrics,
}

impl CaptionProjection {
    pub(crate) fn reset(&mut self, config: Option<ObservedConfig>) {
        self.close_observers();
        self.latest.clear();
        self.latest_bytes = 0;
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
    ) -> Result<Vec<ProjectedFrame>, &'static str> {
        let Some(config) = &self.config else {
            return Ok(Vec::new());
        };
        let mut prepared = Vec::new();
        let mut serial = self.serial;
        for source in sources {
            for (stage, version, committed) in [("stable", 1, false), ("committed", 2, true)] {
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
                    original_text: source.text.clone(),
                    translations: BTreeMap::new(),
                    translation_states: config
                        .targets
                        .iter()
                        .map(|lang| (lang.clone(), "unsupported"))
                        .collect(),
                    asr_provenance: AsrProvenance {
                        provider: "faster-whisper",
                        configured_model: None,
                        verified_model: None,
                        model_revision: None,
                        timestamp_basis: source.timestamp_basis,
                        sample_rate: chunk.sample_rate,
                        audio_chunk_start_sample: chunk.start_sample,
                        audio_chunk_end_sample: chunk.end_sample,
                        auto_generation: None,
                        timeline_offset_samples: None,
                        replay_prefix_samples: None,
                        overlap_trimmed: false,
                    },
                    translation_provenance: BTreeMap::new(),
                    start_ms: source.start_ms,
                    end_ms: source.end_ms,
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
                let raw = RawValue::from_string(
                    serde_json::to_string(&event).map_err(|_| "event_encoding")?,
                )
                .map_err(|_| "event_encoding")?;
                if raw.get().len() > MAX_SNAPSHOT_BYTES - SNAPSHOT_OVERHEAD {
                    return Err("event_limit");
                }
                prepared.push(ProjectedFrame {
                    raw: Arc::from(raw),
                    id: event.segment_id,
                    serial,
                    start_ms: event.start_ms,
                });
            }
        }
        Ok(prepared)
    }

    pub(crate) fn apply(&mut self, prepared: Vec<ProjectedFrame>) {
        for frame in prepared {
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
            self.subscribers.retain(|_, subscriber| {
                if subscriber.frames.try_send(frame.raw.clone()).is_err() {
                    let _ = subscriber.control.send(true);
                    self.metrics.queue_evictions += 1;
                    false
                } else {
                    true
                }
            });
            self.metrics.connected = self.subscribers.len();
        }
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
        let mut visible = self.latest.iter().collect::<Vec<_>>();
        visible.sort_by_key(|frame| (frame.start_ms, frame.serial));
        let json = serde_json::to_string(&Snapshot {
            kind: "snapshot",
            session_id: sid,
            session_generation: generation,
            serial: self.serial,
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
        self.next_client = self
            .next_client
            .checked_add(1)
            .ok_or("client_id_exhausted")?;
        let (tx, rx) = mpsc::channel(OBSERVER_QUEUE);
        tx.try_send(Arc::from(
            RawValue::from_string(json).map_err(|_| "snapshot_encoding")?,
        ))
        .map_err(|_| "snapshot_queue")?;
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
