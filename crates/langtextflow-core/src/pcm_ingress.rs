//! Opt-in continuous PCM ownership; energy monitoring never discards silence.
use crate::asr_slice::{AsrSession, AudioBinding, AudioChunk, MAX_SOURCE_CHUNKS};
use crate::supervision::SupervisedWorker;
use futures_util::future::{select, Either};
use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, Semaphore};
use tokio::task::JoinHandle;

pub const SAMPLE_RATE: u32 = 16_000;
pub const WINDOW_SAMPLES: usize = 4 * SAMPLE_RATE as usize;
pub const FRAME_LIMIT: usize = 1024 * 1024;
pub const QUEUE_FRAMES: usize = 8;
pub const MAX_STREAM_SAMPLES: u64 = MAX_SOURCE_CHUNKS as u64 * WINDOW_SAMPLES as u64;
const REQUEST_BUDGET: Duration = Duration::from_secs(120);
const DRAIN_BUDGET: Duration = Duration::from_secs(10);
const CLEANUP_BUDGET: Duration = Duration::from_secs(2);
const POLL: Duration = Duration::from_millis(10);

#[derive(Debug, Clone, Default, Serialize)]
pub struct IngressMetrics {
    pub accepted_frames: u64,
    pub accepted_samples: u64,
    pub processed_samples: u64,
    pub decoded_windows: u64,
    pub empty_windows: u64,
    pub queue_high_watermark: usize,
    pub queue_depth: usize,
    pub queue_capacity: usize,
    pub backpressure_events: u64,
    pub clipped_samples: u64,
    pub rms_dbfs: Option<f64>,
    pub voice_active: bool,
    pub failure: Option<&'static str>,
}

#[derive(Default)]
pub(crate) struct InputState {
    pub(crate) closing: bool,
    hangover: u8,
    pub(crate) metrics: IngressMetrics,
}

pub(crate) struct Frame {
    pub(crate) pcm: Vec<u8>,
    pub(crate) start_sample: u64,
    pub(crate) accepted_at: Instant,
}
pub(crate) enum Input {
    Frame(Frame),
    End,
}

/// Cloned producers share one ordered admission lease, not another waiting queue.
#[derive(Clone)]
pub struct PcmInput {
    sender: mpsc::Sender<Input>,
    pub(crate) state: Arc<Mutex<InputState>>,
    producer: Arc<Semaphore>,
    pub(crate) session: AsrSession,
    pub(crate) generation: u64,
    pub(crate) source: String,
    capacity: usize,
    max_samples: u64,
}

fn analyze(pcm: &[u8]) -> Result<(f64, u64), &'static str> {
    if pcm.is_empty() || !pcm.len().is_multiple_of(4) || pcm.len() > FRAME_LIMIT {
        return Err("invalid_pcm_frame");
    }
    let mut sum = 0.0_f64;
    let mut clipped = 0;
    for bytes in pcm.chunks_exact(4) {
        let sample = f32::from_le_bytes(bytes.try_into().unwrap());
        if !sample.is_finite() || sample.abs() > 4.0 {
            return Err("invalid_pcm_sample");
        }
        sum += f64::from(sample) * f64::from(sample);
        clipped += u64::from(sample.abs() > 1.0);
    }
    Ok((
        20.0 * (sum / (pcm.len() / 4) as f64).sqrt().max(1e-6).log10(),
        clipped,
    ))
}

impl PcmInput {
    pub(crate) fn channel(
        session: AsrSession,
        source: &str,
        sample_rate: u32,
        capacity: usize,
    ) -> Result<(Self, mpsc::Receiver<Input>), &'static str> {
        if !(8000..=192000).contains(&sample_rate) || !(1..=QUEUE_FRAMES).contains(&capacity) {
            return Err("audio_configuration");
        }
        let generation = session.bind_audio_generation(source)?;
        let (sender, receiver) = mpsc::channel(capacity);
        Ok((
            Self {
                sender,
                state: Arc::new(Mutex::new(InputState::default())),
                producer: Arc::new(Semaphore::new(1)),
                session,
                generation,
                source: source.into(),
                capacity,
                max_samples: MAX_SOURCE_CHUNKS as u64 * 4 * u64::from(sample_rate),
            },
            receiver,
        ))
    }

    pub async fn feed(&self, pcm_f32le: &[u8]) -> Result<(), &'static str> {
        let _producer = self
            .producer
            .clone()
            .try_acquire_owned()
            .map_err(|_| "audio_producer_busy")?;
        let accepted_at = Instant::now();
        let (dbfs, clipped) = analyze(pcm_f32le)?;
        if self.session.audio_generation(&self.source)? != self.generation {
            return Err("stale_audio_generation");
        }
        {
            let state = self.state.lock().unwrap();
            if state.closing {
                return Err(state.metrics.failure.unwrap_or("audio_closed"));
            }
        }
        let backpressure = self.sender.capacity() == 0;
        // No PCM copy or sample-clock mutation until queue capacity is owned.
        // Cancellation of this future cannot admit a late frame.
        let permit = self
            .sender
            .reserve()
            .await
            .map_err(|_| "audio_consumer_stopped")?;
        if self.session.audio_generation(&self.source)? != self.generation {
            return Err("stale_audio_generation");
        }
        let mut state = self.state.lock().unwrap();
        if state.closing {
            return Err(state.metrics.failure.unwrap_or("audio_closed"));
        }
        let count = (pcm_f32le.len() / 4) as u64;
        let start_sample = state.metrics.accepted_samples;
        let end = start_sample
            .checked_add(count)
            .filter(|n| *n <= self.max_samples)
            .ok_or("audio_prototype_capacity")?;
        let frame = Frame {
            pcm: pcm_f32le.to_vec(),
            start_sample,
            accepted_at,
        };
        state.metrics.accepted_samples = end;
        state.metrics.accepted_frames += 1;
        state.metrics.clipped_samples += clipped;
        state.metrics.rms_dbfs = Some(dbfs);
        if dbfs >= -45.0 {
            state.hangover = 3;
            state.metrics.voice_active = true;
        } else if state.hangover > 0 {
            state.hangover -= 1;
            state.metrics.voice_active = true;
        } else {
            state.metrics.voice_active = false;
        }
        state.metrics.backpressure_events += u64::from(backpressure);
        permit.send(Input::Frame(frame));
        let depth = self.capacity - self.sender.capacity();
        state.metrics.queue_high_watermark = state.metrics.queue_high_watermark.max(depth);
        Ok(())
    }

    pub fn metrics(&self) -> IngressMetrics {
        let mut metrics = self.state.lock().unwrap().metrics.clone();
        metrics.queue_depth = self.capacity - self.sender.capacity();
        metrics.queue_capacity = self.capacity;
        metrics
    }
}

/// One owner for input, native child and private spool; no production route selects it.
pub struct PcmIngress {
    input: PcmInput,
    task: Option<JoinHandle<Result<(), &'static str>>>,
    pub(crate) reaped: AsrChildren,
    root: PathBuf,
}

impl PcmIngress {
    pub(crate) fn owned(
        input: PcmInput,
        task: JoinHandle<Result<(), &'static str>>,
        reaped: AsrChildren,
        root: PathBuf,
    ) -> Self {
        Self {
            input,
            task: Some(task),
            reaped,
            root,
        }
    }

    pub fn start(
        session: AsrSession,
        source: &str,
        program: &str,
        args: &[&str],
        slot: Arc<Semaphore>,
        temporary_parent: &Path,
    ) -> Result<Self, &'static str> {
        let (input, receiver) = PcmInput::channel(session, source, SAMPLE_RATE, QUEUE_FRAMES)?;
        // Acquire native ownership before creating a spool. Replacement owners
        // must share this slot and cannot accumulate files while reap is pending.
        let worker =
            SupervisedWorker::spawn(program, args, slot).map_err(|_| "audio_worker_unavailable")?;
        let reaped = AsrChildren::default();
        reaped.track(worker.reaped_signal())?;
        let spool = Spool::new(temporary_parent, reaped.clone())?;
        let root = spool.root.clone();
        let task = tokio::spawn(consume(input.clone(), receiver, worker, spool));
        Ok(Self {
            input,
            task: Some(task),
            reaped,
            root,
        })
    }

    pub fn input(&self) -> PcmInput {
        self.input.clone()
    }

    pub(crate) fn cleanup_path(&self) -> &Path {
        &self.root
    }

    pub async fn finish(mut self) -> Result<IngressMetrics, &'static str> {
        self.input.state.lock().unwrap().closing = true;
        let drain = async {
            let sent = self.input.sender.send(Input::End).await;
            // Keep the task inside self across every await. Dropping an
            // externally timed-out finish future must abort its native owner.
            self.task
                .as_mut()
                .ok_or("audio_owner_missing")?
                .await
                .map_err(|_| "audio_owner_failed")??;
            sent.map_err(|_| "audio_consumer_stopped")
        };
        let result = match tokio::time::timeout(DRAIN_BUDGET, drain).await {
            Ok(result) => {
                self.task.take();
                result
            }
            Err(_) => {
                let task = self.task.take().ok_or("audio_owner_missing")?;
                task.abort();
                let _ = task.await;
                self.input.state.lock().unwrap().metrics.failure = Some("audio_drain_timeout");
                Err("audio_drain_timeout")
            }
        };
        self.confirm_cleanup().await?;
        result?;
        Ok(self.input.metrics())
    }

    pub async fn cancel(mut self) -> Result<IngressMetrics, &'static str> {
        {
            let mut state = self.input.state.lock().unwrap();
            state.closing = true;
            state.metrics.failure.get_or_insert("audio_cancelled");
        }
        if let Some(task) = self.task.take() {
            task.abort();
            let _ = task.await;
        }
        self.confirm_cleanup().await?;
        Ok(self.input.metrics())
    }

    async fn confirm_cleanup(&self) -> Result<(), &'static str> {
        let deadline = Instant::now() + CLEANUP_BUDGET;
        while (!self.reaped.complete() || self.root.exists()) && Instant::now() < deadline {
            tokio::time::sleep(POLL).await;
        }
        if !self.reaped.complete() || self.root.exists() {
            return Err("audio_cleanup_unconfirmed");
        }
        Ok(())
    }
}

impl Drop for PcmIngress {
    fn drop(&mut self) {
        {
            let mut state = self.input.state.lock().unwrap();
            state.closing = true;
            if self.task.is_some() {
                state.metrics.failure.get_or_insert("audio_owner_dropped");
            }
        }
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

fn quantize(pcm: &[u8]) -> Vec<u8> {
    let mut converted = Vec::with_capacity(pcm.len() / 2);
    for bytes in pcm.chunks_exact(4) {
        let value = f64::from(f32::from_le_bytes(bytes.try_into().unwrap())).clamp(-1.0, 1.0);
        converted.extend_from_slice(&((value * 32767.0).round_ties_even() as i16).to_le_bytes());
    }
    converted
}

#[derive(Clone, Default)]
pub(crate) struct AsrChildren(Arc<Mutex<Vec<Arc<AtomicBool>>>>);

impl AsrChildren {
    pub(crate) fn track(&self, reaped: Arc<AtomicBool>) -> Result<(), &'static str> {
        let mut children = self.0.lock().unwrap();
        if children.len() >= 2 || children.iter().any(|child| !child.load(Ordering::Acquire)) {
            return Err("audio_previous_child_unreaped");
        }
        children.push(reaped);
        Ok(())
    }

    pub(crate) fn complete(&self) -> bool {
        self.0
            .lock()
            .unwrap()
            .iter()
            .all(|child| child.load(Ordering::Acquire))
    }
}

pub(crate) struct Spool {
    pub(crate) root: PathBuf,
    active: Option<PathBuf>,
    sequence: u64,
    reaped: AsrChildren,
}

impl Spool {
    pub(crate) fn new(parent: &Path, reaped: AsrChildren) -> Result<Self, &'static str> {
        let parent = parent.canonicalize().map_err(|_| "audio_spool_parent")?;
        let mut random = [0u8; 16];
        getrandom::fill(&mut random).map_err(|_| "audio_spool_random")?;
        let name = random
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let root = parent.join(format!("ltf-pcm-{name}"));
        let mut builder = std::fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(&root).map_err(|_| "audio_spool_create")?;
        Ok(Self {
            root,
            active: None,
            sequence: 0,
            reaped,
        })
    }

    pub(crate) fn write_window(&mut self, pcm: &[u8]) -> Result<PathBuf, &'static str> {
        if self.active.is_some() || pcm.is_empty() || pcm.len() > WINDOW_SAMPLES * 4 {
            return Err("audio_window_shape");
        }
        self.sequence += 1;
        let path = self.root.join(format!("window-{}.wav", self.sequence));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path).map_err(|_| "audio_window_create")?;
        self.active = Some(path.clone());
        let pcm16 = quantize(pcm);
        write_wav(&mut file, &pcm16).map_err(|_| "audio_window_write")?;
        Ok(path)
    }

    pub(crate) fn write_pcm(&mut self, pcm: &[u8]) -> Result<PathBuf, &'static str> {
        if self.active.is_some()
            || pcm.is_empty()
            || !pcm.len().is_multiple_of(4)
            || pcm.len() > FRAME_LIMIT
        {
            return Err("audio_frame_shape");
        }
        self.sequence += 1;
        let path = self.root.join(format!("frame-{}.pcm", self.sequence));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&path).map_err(|_| "audio_frame_create")?;
        self.active = Some(path.clone());
        file.write_all(pcm)
            .and_then(|_| file.flush())
            .map_err(|_| "audio_frame_write")?;
        Ok(path)
    }

    pub(crate) fn clear(&mut self) -> Result<(), &'static str> {
        if let Some(path) = &self.active {
            std::fs::remove_file(path).map_err(|_| "audio_window_remove")?;
            self.active = None;
        }
        Ok(())
    }
}

fn write_wav(file: &mut File, pcm16: &[u8]) -> std::io::Result<()> {
    let bytes = pcm16.len() as u32;
    file.write_all(b"RIFF")?;
    file.write_all(&(36 + bytes).to_le_bytes())?;
    file.write_all(b"WAVEfmt ")?;
    file.write_all(&16u32.to_le_bytes())?;
    file.write_all(&1u16.to_le_bytes())?;
    file.write_all(&1u16.to_le_bytes())?;
    file.write_all(&SAMPLE_RATE.to_le_bytes())?;
    file.write_all(&(SAMPLE_RATE * 2).to_le_bytes())?;
    file.write_all(&2u16.to_le_bytes())?;
    file.write_all(&16u16.to_le_bytes())?;
    file.write_all(b"data")?;
    file.write_all(&bytes.to_le_bytes())?;
    file.write_all(pcm16)?;
    file.flush()
}

impl Drop for Spool {
    fn drop(&mut self) {
        if self
            .active
            .as_ref()
            .is_none_or(|path| std::fs::remove_file(path).is_ok())
        {
            let _ = std::fs::remove_dir(&self.root);
            return;
        }
        // Windows may still hold the killed child's read handle. One native
        // lane owns at most one cleanup thread; no replacement before reap.
        let path = self.active.take().unwrap();
        let root = self.root.clone();
        let reaped = self.reaped.clone();
        let _ = std::thread::Builder::new()
            .name("ltf-audio-spool-cleanup".into())
            .spawn(move || {
                while !reaped.complete() {
                    std::thread::sleep(POLL);
                }
                let _ = std::fs::remove_file(path);
                let _ = std::fs::remove_dir(root);
            });
    }
}

async fn consume(
    input: PcmInput,
    mut receiver: mpsc::Receiver<Input>,
    worker: SupervisedWorker,
    mut spool: Spool,
) -> Result<(), &'static str> {
    let mut worker = Some(worker);
    let mut buffer = Vec::with_capacity(WINDOW_SAMPLES * 4);
    let mut cursor = 0u64;
    let mut sequence = 0u64;
    let mut started = None;
    let result = async {
        loop {
            if input.session.audio_generation(&input.source)? != input.generation {
                return Err("stale_audio_generation");
            }
            let next = match select(
                Box::pin(receiver.recv()),
                Box::pin(tokio::time::sleep(POLL)),
            )
            .await
            {
                Either::Left((next, _)) => next,
                Either::Right(_) => continue,
            };
            match next {
                Some(Input::Frame(frame)) => {
                    if frame.start_sample != cursor + (buffer.len() / 4) as u64 {
                        return Err("audio_clock_discontinuity");
                    }
                    let mut offset = 0;
                    while offset < frame.pcm.len() {
                        if buffer.is_empty() {
                            started = Some(frame.accepted_at);
                        }
                        let take =
                            (WINDOW_SAMPLES * 4 - buffer.len()).min(frame.pcm.len() - offset);
                        buffer.extend_from_slice(&frame.pcm[offset..offset + take]);
                        offset += take;
                        if buffer.len() == WINDOW_SAMPLES * 4 {
                            decode_window(
                                &input,
                                &mut worker,
                                &mut spool,
                                &buffer,
                                cursor,
                                sequence,
                                started.unwrap() + REQUEST_BUDGET,
                            )
                            .await?;
                            cursor += (buffer.len() / 4) as u64;
                            sequence += 1;
                            buffer.clear();
                        }
                    }
                }
                Some(Input::End) => {
                    if !buffer.is_empty() {
                        decode_window(
                            &input,
                            &mut worker,
                            &mut spool,
                            &buffer,
                            cursor,
                            sequence,
                            started.unwrap() + REQUEST_BUDGET,
                        )
                        .await?;
                    }
                    return Ok(());
                }
                None => return Err("audio_input_disappeared"),
            }
        }
    }
    .await;
    receiver.close();
    drop(receiver);
    {
        let mut state = input.state.lock().unwrap();
        state.closing = true;
        if let Err(error) = result {
            state.metrics.failure = Some(error);
        }
    }
    let reaped = if let Some(mut worker) = worker {
        worker.stop().await.map_err(|_| "audio_worker_reap")
    } else {
        Ok(())
    };
    let result = result.and(reaped).and(spool.clear());
    if let Err(error) = result {
        input.state.lock().unwrap().metrics.failure = Some(error);
    }
    result
}

async fn decode_window(
    input: &PcmInput,
    worker: &mut Option<SupervisedWorker>,
    spool: &mut Spool,
    pcm: &[u8],
    start: u64,
    sequence: u64,
    deadline: Instant,
) -> Result<(), &'static str> {
    let path = spool.write_window(pcm)?;
    let samples = (pcm.len() / 4) as u64;
    let admitted = input.session.admit_window(
        &format!("pcm-{}", sequence + 1),
        &input.source,
        AudioChunk {
            wav_path: path.to_string_lossy().into(),
            start_sample: start,
            end_sample: start + samples,
            sample_rate: SAMPLE_RATE,
        },
        &AudioBinding {
            generation: input.generation,
            deadline,
        },
    )?;
    let reply = worker
        .take()
        .ok_or("audio_worker_missing")?
        .exchange(admitted.request(), &admitted.cancelled, admitted.deadline())
        .await
        .map_err(|_| "audio_worker_failed")?;
    *worker = Some(reply.0);
    let events = input.session.finish(&admitted, Some(&reply.1))?;
    let mut state = input.state.lock().unwrap();
    state.metrics.processed_samples += samples;
    state.metrics.decoded_windows += 1;
    state.metrics.empty_windows += u64::from(events.is_empty());
    drop(state);
    spool.clear()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replacement_child_needs_its_predecessors_actual_reap_and_both_proofs_stay_owned() {
        let children = AsrChildren::default();
        let first = Arc::new(AtomicBool::new(false));
        let second = Arc::new(AtomicBool::new(false));
        children.track(first.clone()).unwrap();
        assert!(children.track(second.clone()).is_err());
        assert!(!children.complete());
        first.store(true, Ordering::Release);
        children.track(second.clone()).unwrap();
        assert!(!children.complete());
        second.store(true, Ordering::Release);
        assert!(children.complete());
        assert!(children.track(Arc::new(AtomicBool::new(true))).is_err());
    }

    #[test]
    fn float_validation_rms_and_pcm16_quantization_match_python_contract() {
        for invalid in [
            vec![],
            vec![0],
            vec![0; FRAME_LIMIT + 4],
            f32::NAN.to_le_bytes().to_vec(),
            f32::INFINITY.to_le_bytes().to_vec(),
            4.01f32.to_le_bytes().to_vec(),
        ] {
            assert!(analyze(&invalid).is_err());
        }
        assert_eq!(analyze(&vec![0; FRAME_LIMIT]).unwrap(), (-120.0, 0));
        let pcm = [-4.0f32, -1.0, -0.5, 0.0, 0.5, 1.0, 4.0]
            .into_iter()
            .flat_map(f32::to_le_bytes)
            .collect::<Vec<_>>();
        assert_eq!(analyze(&pcm).unwrap().1, 2);
        let expected = [-32767i16, -32767, -16384, 0, 16384, 32767, 32767]
            .into_iter()
            .flat_map(i16::to_le_bytes)
            .collect::<Vec<_>>();
        assert_eq!(quantize(&pcm), expected);
        assert!((analyze(&0.5f32.to_le_bytes()).unwrap().0 + 6.020599913).abs() < 1e-8);
    }

    #[test]
    fn private_wav_is_bounded_and_cleanup_never_recursively_deletes_unknown_files() {
        let mut spool = Spool::new(&std::env::temp_dir(), AsrChildren::default()).unwrap();
        let root = spool.root.clone();
        let path = spool.write_window(&0.5f32.to_le_bytes()).unwrap();
        let bytes = std::fs::read(&path).unwrap();
        assert_eq!(bytes.len(), 46);
        assert_eq!(&bytes[..4], b"RIFF");
        assert_eq!(&bytes[44..], &16384i16.to_le_bytes());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&root).unwrap().permissions().mode() & 0o777,
                0o700
            );
            assert_eq!(
                std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        let unknown = root.join("unowned-marker");
        std::fs::write(&unknown, b"preserve").unwrap();
        drop(spool);
        assert!(!path.exists());
        assert_eq!(std::fs::read(&unknown).unwrap(), b"preserve");
        std::fs::remove_file(unknown).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
}
