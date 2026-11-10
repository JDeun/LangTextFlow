//! Rust-owned AUTO candidate epochs, bounded replay and private sidecar exchanges.
use crate::supervision::SupervisedWorker;
use crate::{validate_reply, TaskKind, WorkerRequest, WorkerStatus, PROTOCOL_VERSION};
use futures_util::future::{select, Either};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::Semaphore;

pub(crate) async fn while_current<T>(
    session: &crate::asr_slice::AsrSession,
    source: &str,
    generation: u64,
    operation: impl std::future::Future<Output = Result<T, &'static str>>,
) -> Result<T, &'static str> {
    if session.audio_generation(source)? != generation {
        return Err("stale_audio_generation");
    }
    let retired = async {
        loop {
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            if session.audio_generation(source).ok() != Some(generation) {
                break;
            }
        }
    };
    match select(Box::pin(operation), Box::pin(retired)).await {
        Either::Left((result, _)) => result,
        Either::Right(_) => Err("stale_audio_generation"),
    }
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamOptions {
    pub program: String,
    pub args: Vec<String>,
    pub endpoint: String,
}

impl StreamOptions {
    pub fn valid(&self) -> bool {
        !self.program.is_empty()
            && self.program.len() <= 4096
            && !self.program.contains('\0')
            && self.args.len() <= 16
            && self
                .args
                .iter()
                .all(|arg| arg.len() <= 4096 && !arg.contains('\0'))
            && !self.endpoint.is_empty()
            && self.endpoint.len() <= 4096
    }
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AutoOptions {
    pub sidecar: Option<StreamOptions>,
    pub whisper_ready: bool,
    pub low_resource: bool,
    pub replay_seconds: f64,
    pub health_ms: u64,
    pub startup_ms: u64,
    pub queue_frames: usize,
}

impl AutoOptions {
    pub fn valid(&self) -> bool {
        self.replay_seconds.is_finite()
            && self.replay_seconds > 0.
            && self.replay_seconds <= 120.
            && (1..=crate::pcm_ingress::QUEUE_FRAMES).contains(&self.queue_frames)
            && (1..=60_000).contains(&self.health_ms)
            && (1..=600_000).contains(&self.startup_ms)
            && self.sidecar.as_ref().is_none_or(StreamOptions::valid)
            && if self.low_resource {
                self.sidecar.is_none() && self.whisper_ready
            } else {
                self.sidecar.is_some()
            }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamMessage {
    pub text: String,
    pub audio_end_sample: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StreamBatch {
    pub stream_protocol_version: u16,
    pub candidate_epoch: u64,
    pub sample_rate: u32,
    pub chunk_seconds: f64,
    pub start_sample: u64,
    pub transmitted_samples: u64,
    pub messages: Vec<StreamMessage>,
    pub done: bool,
}

/// This client owns only the private model transport. Selection and projection
/// stay in the Rust ingress owner; every exchange carries its immutable epoch.
pub struct StreamClient {
    worker: Option<SupervisedWorker>,
    pub reaped: Arc<AtomicBool>,
    sid: String,
    generation: u64,
    source: String,
    epoch: u64,
    serial: u64,
    pub sample_rate: u32,
    pub chunk_seconds: f64,
    pub transmitted_samples: u64,
    previous_message_sample: u64,
}

impl StreamClient {
    pub async fn open(
        options: &StreamOptions,
        sid: &str,
        generation: u64,
        source: &str,
        epoch: u64,
        slot: Arc<Semaphore>,
        deadline: Instant,
    ) -> Result<Self, &'static str> {
        let mut owner = Self::spawn(options, sid, generation, source, epoch, slot)?;
        owner
            .prepare(
                options,
                &crate::source_context::SourceContext::default(),
                deadline,
            )
            .await?;
        Ok(owner)
    }

    pub fn spawn(
        options: &StreamOptions,
        sid: &str,
        generation: u64,
        source: &str,
        epoch: u64,
        slot: Arc<Semaphore>,
    ) -> Result<Self, &'static str> {
        if !options.valid() || epoch == 0 || epoch > (1 << 53) - 1 {
            return Err("asr_stream_configuration");
        }
        let worker = SupervisedWorker::spawn(
            &options.program,
            &options.args.iter().map(String::as_str).collect::<Vec<_>>(),
            slot,
        )
        .map_err(|_| "asr_stream_unavailable")?;
        Ok(Self {
            reaped: worker.reaped_signal(),
            worker: Some(worker),
            sid: sid.into(),
            generation,
            source: source.into(),
            epoch,
            serial: 0,
            sample_rate: 0,
            chunk_seconds: 0.,
            transmitted_samples: 0,
            previous_message_sample: 0,
        })
    }

    pub async fn prepare(
        &mut self,
        options: &StreamOptions,
        context: &crate::source_context::SourceContext,
        deadline: Instant,
    ) -> Result<(), &'static str> {
        context.validate()?;
        let batch = self
            .exchange(
                "open",
                json!({"endpoint":options.endpoint,"context":context}),
                0,
                deadline,
            )
            .await?;
        if batch.done || !batch.messages.is_empty() {
            return Err("asr_stream_initial_result");
        }
        self.sample_rate = batch.sample_rate;
        self.chunk_seconds = batch.chunk_seconds;
        Ok(())
    }

    pub async fn append(
        &mut self,
        path: &Path,
        count: u64,
        deadline: Instant,
    ) -> Result<StreamBatch, &'static str> {
        let end = self
            .transmitted_samples
            .checked_add(count)
            .ok_or("asr_stream_clock")?;
        self.exchange(
            "append",
            json!({"pcm_path":path,"start_sample":self.transmitted_samples,
                "end_sample":end,"sample_rate":self.sample_rate}),
            end,
            deadline,
        )
        .await
    }

    pub async fn poll(
        &mut self,
        finish: bool,
        deadline: Instant,
    ) -> Result<StreamBatch, &'static str> {
        self.exchange(
            if finish { "finish" } else { "poll" },
            json!({"sample_rate":self.sample_rate,"start_sample":self.transmitted_samples,
                "end_sample":self.transmitted_samples}),
            self.transmitted_samples,
            deadline,
        )
        .await
    }

    async fn exchange(
        &mut self,
        mode: &str,
        extra: Value,
        expected_end: u64,
        deadline: Instant,
    ) -> Result<StreamBatch, &'static str> {
        self.serial = self.serial.checked_add(1).ok_or("asr_stream_serial")?;
        let mut payload = json!({"stream_protocol_version":1,"candidate_epoch":self.epoch,
            "mode":mode});
        payload
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        let request = WorkerRequest {
            protocol_version: PROTOCOL_VERSION,
            request_id: format!("stream-{}-{}", self.epoch, self.serial),
            session_id: self.sid.clone(),
            session_generation: self.generation,
            segment_id: format!("stream-{}", self.epoch),
            source_revision: 1,
            task_kind: TaskKind::Asr,
            source_language: self.source.clone(),
            target_language: None,
            deadline_ms: deadline
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(if mode == "open" { 600_000 } else { 120_000 })
                as u64,
            payload: payload.to_string(),
        };
        let (worker, reply) = self
            .worker
            .take()
            .ok_or("asr_stream_closed")?
            .exchange(&request, &AtomicBool::new(false), deadline)
            .await
            .map_err(|_| "asr_stream_failed")?;
        self.worker = Some(worker);
        validate_reply(&request, &reply, self.generation, 1).map_err(|_| "asr_stream_identity")?;
        if reply.status != WorkerStatus::Ok {
            return Err("asr_stream_failed");
        }
        let raw = reply.result.as_deref().ok_or("asr_stream_result")?;
        let batch: StreamBatch = serde_json::from_str(raw).map_err(|_| "asr_stream_result")?;
        if raw.len() > 96 * 1024
            || batch.stream_protocol_version != 1
            || batch.candidate_epoch != self.epoch
            || !(8000..=192000).contains(&batch.sample_rate)
            || !batch.chunk_seconds.is_finite()
            || !(0.05..=60.).contains(&batch.chunk_seconds)
            || (self.sample_rate != 0
                && (batch.sample_rate != self.sample_rate
                    || batch.chunk_seconds != self.chunk_seconds))
            || batch.start_sample != self.transmitted_samples
            || batch.transmitted_samples != expected_end
            || expected_end > (1_u64 << 53) / 1000
            || batch.messages.len() > 128
        {
            return Err("asr_stream_result");
        }
        for message in &batch.messages {
            if message.text.trim().is_empty()
                || message.text.chars().count() > 20_000
                || message.audio_end_sample == 0
                || message.audio_end_sample > expected_end
                || message.audio_end_sample < self.previous_message_sample
            {
                return Err("asr_stream_result");
            }
            self.previous_message_sample = message.audio_end_sample;
        }
        self.transmitted_samples = expected_end;
        Ok(batch)
    }

    pub async fn stop(&mut self) -> Result<(), &'static str> {
        if let Some(mut worker) = self.worker.take() {
            worker.stop().await.map_err(|_| "asr_stream_reap")?;
        }
        confirm_reaped(&self.reaped)
            .await
            .map_err(|_| "asr_stream_reap")
    }
}

pub struct WhisperClient {
    worker: Option<SupervisedWorker>,
    pub reaped: Arc<AtomicBool>,
    sid: String,
    generation: u64,
    source: String,
    epoch: u64,
    serial: u64,
}

impl WhisperClient {
    pub(crate) fn into_worker(mut self) -> Result<SupervisedWorker, &'static str> {
        self.worker.take().ok_or("asr_whisper_closed")
    }

    pub async fn open(
        program: &str,
        args: &[&str],
        sid: &str,
        generation: u64,
        source: &str,
        epoch: u64,
        slot: Arc<Semaphore>,
        deadline: Instant,
    ) -> Result<Self, &'static str> {
        let mut client = Self::spawn(program, args, sid, generation, source, epoch, slot)?;
        client.prepare(deadline).await?;
        Ok(client)
    }

    pub fn spawn(
        program: &str,
        args: &[&str],
        sid: &str,
        generation: u64,
        source: &str,
        epoch: u64,
        slot: Arc<Semaphore>,
    ) -> Result<Self, &'static str> {
        if epoch == 0 || epoch > (1 << 53) - 1 {
            return Err("asr_whisper_configuration");
        }
        let worker =
            SupervisedWorker::spawn(program, args, slot).map_err(|_| "asr_whisper_unavailable")?;
        Ok(Self {
            reaped: worker.reaped_signal(),
            worker: Some(worker),
            sid: sid.into(),
            generation,
            source: source.into(),
            epoch,
            serial: 0,
        })
    }

    pub async fn prepare(&mut self, deadline: Instant) -> Result<(), &'static str> {
        let result = self
            .exchange(json!({"asr_protocol_version":1,"mode":"prepare"}), deadline)
            .await?;
        if result != json!({"asr_protocol_version":1,"prepared":true,"sample_rate":16000}) {
            return Err("asr_whisper_prepare");
        }
        Ok(())
    }

    pub async fn infer(
        &mut self,
        chunk: &crate::asr_slice::AudioChunk,
        context: &crate::source_context::SourceContext,
        deadline: Instant,
    ) -> Result<crate::asr_slice::AsrResult, &'static str> {
        if chunk.sample_rate != 16000
            || chunk.end_sample <= chunk.start_sample
            || chunk.end_sample - chunk.start_sample > 120 * 16000
            || chunk.wav_path.is_empty()
            || chunk.wav_path.len() > 4096
        {
            return Err("asr_whisper_descriptor");
        }
        let mut payload = serde_json::to_value(chunk).map_err(|_| "asr_whisper_descriptor")?;
        payload.as_object_mut().unwrap().insert(
            "context".into(),
            serde_json::to_value(context).map_err(|_| "asr_whisper_context")?,
        );
        let value = self.exchange(payload, deadline).await?;
        let result: crate::asr_slice::AsrResult =
            serde_json::from_value(value).map_err(|_| "asr_whisper_result")?;
        let samples = chunk.end_sample - chunk.start_sample;
        if result.sample_rate != 16000
            || result.sample_count != samples
            || result.segments.len() > 256
        {
            return Err("asr_whisper_result");
        }
        let duration = samples as f64 / 16000.;
        if result.segments.iter().any(|s| {
            !s.start_seconds.is_finite()
                || !s.end_seconds.is_finite()
                || s.start_seconds < -0.25
                || s.end_seconds < s.start_seconds
                || s.end_seconds > duration + 0.25
                || s.text.trim().is_empty()
                || s.text.chars().count() > 20000
        }) {
            return Err("asr_whisper_result");
        }
        Ok(result)
    }

    async fn exchange(&mut self, payload: Value, deadline: Instant) -> Result<Value, &'static str> {
        self.serial = self.serial.checked_add(1).ok_or("asr_whisper_serial")?;
        let request = WorkerRequest {
            protocol_version: PROTOCOL_VERSION,
            request_id: format!("auto-whisper-{}-{}", self.epoch, self.serial),
            session_id: self.sid.clone(),
            session_generation: self.generation,
            segment_id: format!("auto-whisper-{}", self.epoch),
            source_revision: 1,
            task_kind: TaskKind::Asr,
            source_language: self.source.clone(),
            target_language: None,
            deadline_ms: deadline
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(
                    if payload.get("mode").and_then(Value::as_str) == Some("prepare") {
                        600_000
                    } else {
                        120_000
                    },
                ) as u64,
            payload: payload.to_string(),
        };
        let (worker, reply) = self
            .worker
            .take()
            .ok_or("asr_whisper_closed")?
            .exchange(&request, &AtomicBool::new(false), deadline)
            .await
            .map_err(|_| "asr_whisper_failed")?;
        self.worker = Some(worker);
        validate_reply(&request, &reply, self.generation, 1).map_err(|_| "asr_whisper_identity")?;
        if reply.status != WorkerStatus::Ok {
            return Err("asr_whisper_failed");
        }
        serde_json::from_str(reply.result.as_deref().ok_or("asr_whisper_result")?)
            .map_err(|_| "asr_whisper_result")
    }

    pub async fn stop(&mut self) -> Result<(), &'static str> {
        if let Some(mut worker) = self.worker.take() {
            worker.stop().await.map_err(|_| "asr_whisper_reap")?;
        }
        confirm_reaped(&self.reaped)
            .await
            .map_err(|_| "asr_whisper_reap")
    }
}

async fn confirm_reaped(reaped: &AtomicBool) -> Result<(), ()> {
    let deadline = Instant::now() + std::time::Duration::from_secs(2);
    while !reaped.load(Ordering::Acquire) {
        if Instant::now() >= deadline {
            return Err(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    Ok(())
}

/// A byte ring also bounds tiny-frame metadata. Input clock always includes the
/// entire accepted frame even when only its exact tail remains eligible to replay.
pub struct ReplayPcm {
    pcm: VecDeque<u8>,
    limit: usize,
    pub accepted_samples: u64,
}

impl ReplayPcm {
    pub fn new(sample_rate: u32, replay_ms: u64) -> Result<Self, &'static str> {
        Self::new_seconds(sample_rate, replay_ms as f64 / 1000.)
    }

    pub fn new_seconds(sample_rate: u32, seconds: f64) -> Result<Self, &'static str> {
        if !(8000..=192000).contains(&sample_rate)
            || !seconds.is_finite()
            || seconds <= 0.
            || seconds > 120.
        {
            return Err("asr_replay_configuration");
        }
        let bytes = (seconds * f64::from(sample_rate) * 4.) as usize;
        let limit = (bytes - bytes % 4).max(4);
        Ok(Self {
            pcm: VecDeque::new(),
            limit,
            accepted_samples: 0,
        })
    }

    pub fn push(&mut self, pcm: &[u8]) -> Result<(), &'static str> {
        if pcm.is_empty() || !pcm.len().is_multiple_of(4) {
            return Err("asr_replay_pcm");
        }
        self.accepted_samples = self
            .accepted_samples
            .checked_add((pcm.len() / 4) as u64)
            .ok_or("asr_replay_clock")?;
        let tail = &pcm[pcm.len().saturating_sub(self.limit)..];
        let remove = (self.pcm.len() + tail.len()).saturating_sub(self.limit);
        self.pcm.drain(..remove.min(self.pcm.len()));
        self.pcm.extend(tail);
        Ok(())
    }

    pub fn snapshot(&self) -> (u64, Vec<u8>) {
        (
            self.accepted_samples - (self.pcm.len() / 4) as u64,
            self.pcm.iter().copied().collect(),
        )
    }
}

#[derive(Default, Debug, Clone, Serialize)]
pub struct AutoStatus {
    pub active_provider: Option<&'static str>,
    pub auto_generation: u64,
    pub failover_count: u64,
    pub last_failover_reason: Option<FailoverReason>,
}

#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailoverReason {
    ProviderFailed,
    ProviderClosed,
    ProviderTimeout,
    ProviderProtocol,
    ProviderEnded,
}

fn python_space(ch: char) -> bool {
    ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch)
}

/// Compare equal counts of original characters, exactly like the Python path.
/// Case-fold expansion never becomes an extra overlap or rewrites retained text.
pub fn strip_exact_overlap(previous: &str, current: &str) -> String {
    let left = previous.trim_matches(python_space);
    let right = current.trim_matches(python_space);
    let a = left.chars().collect::<Vec<_>>();
    let b = right
        .char_indices()
        .map(|(i, _)| i)
        .chain([right.len()])
        .collect::<Vec<_>>();
    let limit = a.len().min(b.len() - 1).min(240);
    for size in (4..=limit).rev() {
        let suffix = a[a.len() - size..].iter().collect::<String>();
        if caseless::default_case_fold_str(&suffix)
            == caseless::default_case_fold_str(&right[..b[size]])
        {
            return right[b[size]..]
                .trim_start_matches([' ', '\t', '\n', ',', '.', ';', ':', '!', '?', '…', '-', '—'])
                .to_owned();
        }
    }
    right.to_owned()
}

#[derive(Debug, Clone)]
pub struct MappedText {
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub overlap_trimmed: bool,
}

/// Session sample clock survives provider replacement. Local provider estimates
/// and replay origin remain separate; neither becomes native word alignment.
#[derive(Clone, Default)]
pub struct AutoClock {
    pub sample_rate: u32,
    pub generation: u64,
    pub origin_samples: u64,
    pub replay_prefix_samples: u64,
    sequence: u64,
    local_end_ms: u64,
    last_published_end_ms: u64,
    recent_text: String,
}

impl AutoClock {
    pub fn candidate(
        &mut self,
        rate: u32,
        generation: u64,
        origin: u64,
        replay: u64,
    ) -> Result<(), &'static str> {
        if !(8000..=192000).contains(&rate) || (self.sample_rate != 0 && rate != self.sample_rate) {
            return Err("asr_sample_rate_mismatch");
        }
        if generation > 1
            || origin > (1_u64 << 53) / 1000
            || replay > 120 * u64::from(rate)
            || (generation == 0 && (origin != 0 || replay != 0))
        {
            return Err("asr_replay_clock");
        }
        self.sample_rate = rate;
        self.generation = generation;
        self.origin_samples = origin;
        self.replay_prefix_samples = replay;
        self.sequence = 0;
        self.local_end_ms = 0;
        Ok(())
    }

    pub fn sidecar(
        &mut self,
        message: StreamMessage,
        seconds: f64,
    ) -> Result<Option<MappedText>, &'static str> {
        self.sequence = self.sequence.checked_add(1).ok_or("asr_stream_sequence")?;
        if self.sample_rate == 0
            || message.audio_end_sample == 0
            || !seconds.is_finite()
            || !(0.05..=60.).contains(&seconds)
        {
            return Err("asr_stream_clock");
        }
        let audio_end = (message.audio_end_sample as f64 * 1000. / self.sample_rate as f64)
            .round_ties_even() as u64;
        let nominal_start = ((self.sequence - 1) as f64 * seconds * 1000.).round_ties_even() as u64;
        let nominal_end = (self.sequence as f64 * seconds * 1000.).round_ties_even() as u64;
        let start = self.local_end_ms.max(audio_end.min(nominal_start));
        let end = start.max(audio_end.min(nominal_end));
        self.local_end_ms = end;
        self.map(message.text, start, end)
    }

    pub fn map(
        &mut self,
        text: String,
        start_ms: u64,
        end_ms: u64,
    ) -> Result<Option<MappedText>, &'static str> {
        if self.sample_rate == 0
            || end_ms < start_ms
            || text.trim_matches(python_space).is_empty()
            || text.chars().count() > 20_000
        {
            return Err("asr_source_clock");
        }
        let offset = self.origin_samples as f64 * 1000. / self.sample_rate as f64;
        let mut start = (offset + start_ms as f64).round_ties_even() as u64;
        let end = start.max((offset + end_ms as f64).round_ties_even() as u64);
        let mut text = text.trim_matches(python_space).to_owned();
        let mut trimmed = false;
        if self.generation > 0 {
            if end <= self.last_published_end_ms {
                return Ok(None);
            }
            if start < self.last_published_end_ms {
                text = strip_exact_overlap(&self.recent_text, &text);
                if text.is_empty() {
                    return Ok(None);
                }
                start = self.last_published_end_ms;
                trimmed = true;
            }
        }
        self.last_published_end_ms = self.last_published_end_ms.max(end);
        self.recent_text.push(' ');
        self.recent_text.push_str(&text);
        self.recent_text = self
            .recent_text
            .chars()
            .rev()
            .take(1000)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        Ok(Some(MappedText {
            text,
            start_ms: start,
            end_ms: end,
            overlap_trimmed: trimmed,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn replay_bounds_oversized_and_tiny_frames_without_losing_the_true_clock() {
        let mut replay = ReplayPcm::new(48000, 10).unwrap();
        let pcm = (0u32..2000).flat_map(u32::to_le_bytes).collect::<Vec<_>>();
        replay.push(&pcm).unwrap();
        let (origin, bytes) = replay.snapshot();
        assert_eq!(origin, 1520);
        assert_eq!(bytes, pcm[1520 * 4..]);
        for _ in 0..1000 {
            replay.push(&[0; 4]).unwrap();
        }
        let (origin, bytes) = replay.snapshot();
        assert_eq!(origin, 2520);
        assert_eq!(bytes, vec![0; 480 * 4]);
    }

    #[test]
    fn real_python_overlap_corpus_preserves_full_folding_original_counts_and_text() {
        let pairs = [
            ("old abßc", "ABẞC — Keep Case"),
            ("old aﬃbc", "AﬃBC: next"),
            ("old Straße", "STRASSE next"),
            ("old 은혜로구원", "은혜로구원, 다음 문장"),
            ("old 主の恵みです", "主の恵みです。次の文"),
            ("old 𐐀abc", "𐐨ABC Next"),
            ("old Ꭰabc", "ꭰABC Next"),
            ("old Σabc", "ςABC Next"),
            ("old abc", "ABC next"),
            ("old aßc", "AẞC next"),
            ("old café", "CAFE\u{301} next"),
            ("\u{1c}alpha\u{1f}", "\u{1f}ALPHA next\u{1c}"),
        ];
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let python = std::env::var_os("LTF_HISTORY_PYTHON")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| {
                root.join(if cfg!(windows) {
                    ".venv/Scripts/python.exe"
                } else {
                    ".venv/bin/python"
                })
            });
        let script = r#"
import json,sys,unicodedata
from langtextflow.asr.fallback import _strip_exact_text_overlap
print(json.dumps({'unicode_version':unicodedata.unidata_version,
 'results':[_strip_exact_text_overlap(a,b) for a,b in json.loads(sys.argv[1])]},ensure_ascii=True))
"#;
        let output = std::process::Command::new(python)
            .args(["-c", script, &serde_json::to_string(&pairs).unwrap()])
            .env(
                "PYTHONPATH",
                std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../apps/server"),
            )
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let result: Value = serde_json::from_slice(&output.stdout).unwrap();
        for ((a, b), expected) in pairs.iter().zip(result["results"].as_array().unwrap()) {
            assert_eq!(
                strip_exact_overlap(a, b),
                expected.as_str().unwrap(),
                "{a:?} {b:?}"
            );
        }
        assert_eq!(
            strip_exact_overlap("old abßc", "ABẞC — Keep Case"),
            "Keep Case"
        );
        assert_eq!(
            strip_exact_overlap("old Straße", "STRASSE next"),
            "STRASSE next"
        );
        assert_eq!(strip_exact_overlap("old aßc", "AẞC next"), "AẞC next");
        assert_eq!(caseless::UNICODE_VERSION, (16, 0, 0));
    }

    #[test]
    fn clock_keeps_48k_sequence_estimates_monotonic_and_failover_overlap_conservative() {
        let mut clock = AutoClock::default();
        clock.candidate(48000, 0, 0, 0).unwrap();
        let first = clock
            .sidecar(
                StreamMessage {
                    text: "source One".into(),
                    audio_end_sample: 1200,
                },
                2.,
            )
            .unwrap()
            .unwrap();
        assert_eq!((first.start_ms, first.end_ms), (0, 25));
        let next = clock
            .sidecar(
                StreamMessage {
                    text: "source Two".into(),
                    audio_end_sample: 2400,
                },
                2.,
            )
            .unwrap()
            .unwrap();
        assert_eq!((next.start_ms, next.end_ms), (50, 50));
        assert!(clock.candidate(16000, 1, 0, 2400).is_err());
        clock.candidate(48000, 1, 1200, 1200).unwrap();
        assert!(clock.map("duplicate".into(), 0, 25).unwrap().is_none());
        let kept = clock
            .map("source Two, next".into(), 0, 50)
            .unwrap()
            .unwrap();
        assert_eq!((kept.start_ms, kept.end_ms), (50, 75));
        assert_eq!(kept.text, "next");
        assert!(kept.overlap_trimmed);
    }

    #[test]
    fn fractional_replay_seconds_keep_the_existing_float_to_complete_pcm_sample_policy() {
        let mut tiny = ReplayPcm::new_seconds(16000, 0.0001).unwrap();
        tiny.push(&[0; 32]).unwrap();
        assert_eq!(tiny.snapshot(), (7, vec![0; 4]));
        let mut fractional = ReplayPcm::new_seconds(48000, 0.0251).unwrap();
        fractional.push(&vec![0; 1300 * 4]).unwrap();
        assert_eq!(fractional.snapshot(), (96, vec![0; 1204 * 4]));
        for bad in [0., -0.1, f64::NAN, f64::INFINITY, 120.01] {
            assert!(ReplayPcm::new_seconds(16000, bad).is_err());
        }
    }

    #[test]
    fn low_policy_can_never_carry_a_sidecar_or_an_unprepared_whisper() {
        let mut options = AutoOptions {
            queue_frames: 3,
            sidecar: None,
            whisper_ready: true,
            low_resource: true,
            replay_seconds: 8.,
            health_ms: 250,
            startup_ms: 120000,
        };
        assert!(options.valid());
        options.whisper_ready = false;
        assert!(!options.valid());
        options.whisper_ready = true;
        options.sidecar = Some(StreamOptions {
            program: "python".into(),
            args: vec![],
            endpoint: "http://127.0.0.1:8001".into(),
        });
        assert!(!options.valid());
    }

    #[test]
    fn actual_stream_child_results_require_the_current_identity_epoch_and_sample_range() {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let python =
                    std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python3".into());
                let script = r#"
import json,sys
sample=0
for line in sys.stdin:
 r=json.loads(line);p=json.loads(r['payload']);start=sample
 if p['mode']=='append': sample=p['end_sample']
 b={'stream_protocol_version':1,'candidate_epoch':p['candidate_epoch'],
  'sample_rate':48000,'chunk_seconds':2.0,'start_sample':start,
  'transmitted_samples':sample,'messages':[],'done':p['mode']=='finish'}
 mode=sys.argv[1]
 if mode=='epoch':b['candidate_epoch']+=1
 if mode=='clock':b['transmitted_samples']+=1
 if mode=='rate':b['sample_rate']=0
 if mode=='message':b['messages']=[{'text':'before PCM','audio_end_sample':0}]
 if mode=='flood':b['messages']=[{'text':'x','audio_end_sample':1}]*129
 out={k:r[k] for k in ('protocol_version','request_id','session_id',
  'session_generation','segment_id','source_revision')}
 if mode=='identity':out['request_id']='old-request'
 out.update(status='ok',result=json.dumps(b),error=None)
 print(json.dumps(out),flush=True)
"#;
                for mode in [
                    "normal", "epoch", "clock", "rate", "message", "flood", "identity",
                ] {
                    let slot = crate::supervision::worker_slot();
                    let options = StreamOptions {
                        program: python.clone(),
                        args: vec!["-u".into(), "-c".into(), script.into(), mode.into()],
                        endpoint: "http://127.0.0.1:8001".into(),
                    };
                    let result = StreamClient::open(
                        &options,
                        "session",
                        4,
                        "ja",
                        7,
                        slot.clone(),
                        Instant::now() + Duration::from_secs(2),
                    )
                    .await;
                    if mode == "normal" {
                        let mut client = result.unwrap();
                        assert_eq!(client.sample_rate, 48000);
                        let appended = client
                            .append(
                                Path::new("private.pcm"),
                                1600,
                                Instant::now() + Duration::from_secs(1),
                            )
                            .await
                            .unwrap();
                        assert_eq!(appended.start_sample, 0);
                        assert_eq!(appended.transmitted_samples, 1600);
                        assert!(
                            client
                                .poll(true, Instant::now() + Duration::from_secs(1))
                                .await
                                .unwrap()
                                .done
                        );
                        client.stop().await.unwrap();
                        assert!(client.reaped.load(Ordering::Acquire));
                    } else {
                        assert!(result.is_err(), "{mode}");
                    }
                    tokio::time::timeout(Duration::from_secs(2), async {
                        while slot.available_permits() == 0 {
                            tokio::time::sleep(Duration::from_millis(5)).await;
                        }
                    })
                    .await
                    .unwrap();
                }
            });
    }

    #[test]
    fn whisper_preparation_cannot_accept_an_old_worker_asr_result_as_a_ready_ack() {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let python=std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_|"python3".into());
            let script=r#"
import json,sys
for line in sys.stdin:
 r=json.loads(line);p=json.loads(r['payload']); mode=sys.argv[1]
 if p.get('mode')=='prepare' and mode!='old-worker':
  result={'asr_protocol_version':1,'prepared':True,'sample_rate':16000}
 else:
  result={'sample_rate':16000,'sample_count':3 if mode=='bad-result' else 2,
   'segments':[{'start_seconds':0.,'end_seconds':.000125,'text':'source','estimated_timestamps':False}]}
 out={k:r[k] for k in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
 out.update(status='ok',result=json.dumps(result),error=None);print(json.dumps(out),flush=True)
"#;
            for mode in ["normal","old-worker","bad-result"] {
                let slot=crate::supervision::worker_slot();
                let result=WhisperClient::open(&python,&["-u","-c",script,mode],"session",3,"ko",2,
                    slot.clone(),Instant::now()+Duration::from_secs(2)).await;
                if mode=="old-worker" { assert!(result.is_err()); } else {
                    let mut client=result.unwrap();
                    let result=client.infer(&crate::asr_slice::AudioChunk {wav_path:"owned.wav".into(),
                        start_sample:0,end_sample:2,sample_rate:16000},&Default::default(),
                        Instant::now()+Duration::from_secs(1)).await;
                    assert_eq!(result.is_ok(),mode=="normal");
                    client.stop().await.unwrap();
                }
                tokio::time::timeout(Duration::from_secs(2),async {
                    while slot.available_permits()==0 {tokio::time::sleep(Duration::from_millis(5)).await;}
                }).await.unwrap();
            }
        });
    }
}
