//! One-way AUTO source ownership; workers never select providers or publish events.
use crate::asr_slice::{AsrSession, AudioChunk, AutoSource};
use crate::auto_asr::{
    AutoClock, AutoOptions, FailoverReason, ReplayPcm, StreamBatch, StreamClient, WhisperClient,
};
use crate::pcm_ingress::{AsrChildren, Input, PcmIngress, PcmInput, Spool, WINDOW_SAMPLES};
use crate::source_context::SourceContext;
use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, Semaphore};

enum Candidate {
    Stream(StreamClient),
    Whisper(WhisperClient),
}

impl Candidate {
    async fn stop(&mut self) -> Result<(), &'static str> {
        match self {
            Self::Stream(client) => client.stop().await,
            Self::Whisper(client) => client.stop().await,
        }
    }
}

struct Owner {
    candidate: Candidate,
    options: AutoOptions,
    whisper_program: String,
    whisper_args: Vec<String>,
    slot: Arc<Semaphore>,
    children: AsrChildren,
    sid: String,
    generation: u64,
    epoch: u64,
    context: Arc<SourceContext>,
    clock: AutoClock,
    ring: ReplayPcm,
    buffer: Vec<u8>,
    cursor: u64,
    sequence: u64,
    window_started: Option<Instant>,
}

pub(crate) async fn start(
    session: AsrSession,
    source: &str,
    options: AutoOptions,
    whisper_program: &str,
    whisper_args: &[&str],
    slot: Arc<Semaphore>,
    temporary_parent: &Path,
) -> Result<PcmIngress, &'static str> {
    if !options.valid() {
        return Err("auto_configuration");
    }
    let (sid, generation, context) = session.auto_source_scope(source)?;
    let children = AsrChildren::default();
    let mut epoch = 1;
    session.begin_auto_candidate(generation, epoch)?;
    let mut selected = None;
    if let Some(sidecar) = &options.sidecar {
        if let Ok(mut client) =
            StreamClient::spawn(sidecar, &sid, generation, source, epoch, slot.clone())
        {
            children.track(client.reaped.clone())?;
            match client
                .prepare(
                    sidecar,
                    Instant::now() + Duration::from_millis(options.startup_ms),
                )
                .await
            {
                Ok(()) => selected = Some(Candidate::Stream(client)),
                Err(_) => {
                    client.stop().await?;
                }
            }
        }
    }
    if selected.is_none() {
        if !options.whisper_ready {
            return Err("auto_candidates_unavailable");
        }
        if options.sidecar.is_some() {
            epoch += 1;
            session.begin_auto_candidate(generation, epoch)?;
        }
        let mut client = WhisperClient::spawn(
            whisper_program,
            whisper_args,
            &sid,
            generation,
            source,
            epoch,
            slot.clone(),
        )?;
        children.track(client.reaped.clone())?;
        if let Err(error) = client
            .prepare(Instant::now() + Duration::from_millis(options.startup_ms))
            .await
        {
            client.stop().await?;
            return Err(error);
        }
        selected = Some(Candidate::Whisper(client));
    }
    let candidate = selected.unwrap();
    let (provider, rate) = match &candidate {
        Candidate::Stream(client) => ("vibevoice", client.sample_rate),
        Candidate::Whisper(_) => ("faster-whisper", 16000),
    };
    let mut clock = AutoClock::default();
    clock.candidate(rate, 0, 0, 0)?;
    let (input, receiver) = PcmInput::channel(session.clone(), source, rate, options.queue_frames)?;
    let spool = Spool::new(temporary_parent, children.clone())?;
    let root = spool.root.clone();
    session.activate_auto_candidate(generation, epoch, provider, &clock, None)?;
    let replay_ms = options.replay_ms;
    let owner = Owner {
        candidate,
        options,
        whisper_program: whisper_program.into(),
        whisper_args: whisper_args.iter().map(|arg| (*arg).into()).collect(),
        slot,
        children: children.clone(),
        sid,
        generation,
        epoch,
        context,
        clock,
        ring: ReplayPcm::new(rate, replay_ms)?,
        buffer: Vec::with_capacity(WINDOW_SAMPLES * 4),
        cursor: 0,
        sequence: 0,
        window_started: None,
    };
    let task = tokio::spawn(consume(owner, input.clone(), receiver, spool));
    Ok(PcmIngress::owned(input, task, children, root))
}

async fn consume(
    mut owner: Owner,
    input: PcmInput,
    mut receiver: mpsc::Receiver<Input>,
    mut spool: Spool,
) -> Result<(), &'static str> {
    let mut next_health = Instant::now() + Duration::from_millis(owner.options.health_ms);
    let result = async {
        loop {
            if input.session.audio_generation(&input.source)? != owner.generation {
                return Err("stale_audio_generation");
            }
            let next = tokio::select! {
                next = receiver.recv() => Some(next),
                _ = tokio::time::sleep(Duration::from_millis(10)) => None,
            };
            match next {
                Some(Some(Input::Frame(frame))) => {
                    if frame.start_sample != owner.ring.accepted_samples {
                        return Err("audio_clock_discontinuity");
                    }
                    owner.ring.push(&frame.pcm)?;
                    let result = owner
                        .feed(
                            &input,
                            &mut spool,
                            &frame.pcm,
                            frame.accepted_at + Duration::from_secs(120),
                        )
                        .await;
                    if result.is_err() {
                        owner
                            .failover(&input, &mut spool, FailoverReason::ProviderFailed)
                            .await?;
                    }
                    next_health = Instant::now() + Duration::from_millis(owner.options.health_ms);
                }
                Some(Some(Input::End)) => {
                    owner.finish(&input, &mut spool).await?;
                    return Ok(());
                }
                Some(None) => return Err("audio_input_disappeared"),
                None if Instant::now() >= next_health => {
                    if let Candidate::Stream(client) = &mut owner.candidate {
                        let batch = client
                            .poll(false, Instant::now() + Duration::from_secs(1))
                            .await;
                        match batch {
                            Ok(batch) => {
                                let done = batch.done;
                                owner.publish_stream(&input, batch)?;
                                if done {
                                    owner
                                        .failover(&input, &mut spool, FailoverReason::ProviderEnded)
                                        .await?;
                                }
                            }
                            Err(_) => {
                                owner
                                    .failover(&input, &mut spool, FailoverReason::ProviderClosed)
                                    .await?
                            }
                        }
                    }
                    next_health = Instant::now() + Duration::from_millis(owner.options.health_ms);
                }
                None => (),
            }
        }
    }
    .await;
    receiver.close();
    drop(receiver);
    input
        .session
        .auto_unavailable(owner.generation, owner.epoch);
    let stopped = owner.candidate.stop().await;
    let result = result.and(stopped).and(spool.clear());
    let mut state = input.state.lock().unwrap();
    state.closing = true;
    if let Err(error) = result {
        state.metrics.failure = Some(error);
    }
    result
}

impl Owner {
    fn publish_stream(&mut self, input: &PcmInput, batch: StreamBatch) -> Result<(), &'static str> {
        let mut clock = self.clock.clone();
        let mut sources = Vec::new();
        for message in batch.messages {
            let end = message.audio_end_sample;
            if let Some(mapped) = clock.sidecar(message, batch.chunk_seconds)? {
                sources.push(AutoSource {
                    mapped,
                    timestamp_basis: "sequence_estimated",
                    chunk_start: None,
                    chunk_end: end,
                });
            }
        }
        if !sources.is_empty() {
            self.publish(input, sources)?;
        }
        self.clock = clock;
        Ok(())
    }

    fn publish(&mut self, input: &PcmInput, sources: Vec<AutoSource>) -> Result<(), &'static str> {
        self.sequence += 1;
        input.session.accept_auto_sources(
            self.generation,
            self.epoch,
            &format!("auto-{}-{}", self.epoch, self.sequence),
            sources,
            Instant::now() + Duration::from_secs(120),
        )?;
        Ok(())
    }

    async fn feed(
        &mut self,
        input: &PcmInput,
        spool: &mut Spool,
        pcm: &[u8],
        deadline: Instant,
    ) -> Result<(), &'static str> {
        if let Candidate::Stream(client) = &mut self.candidate {
            let path = spool.write_pcm(pcm)?;
            input
                .session
                .auto_window(self.generation, self.epoch, true)?;
            let batch = client
                .append(&path, (pcm.len() / 4) as u64, deadline)
                .await?;
            input
                .session
                .auto_window(self.generation, self.epoch, false)?;
            spool.clear()?;
            let done = batch.done;
            self.publish_stream(input, batch)?;
            input.state.lock().unwrap().metrics.processed_samples += (pcm.len() / 4) as u64;
            return if done {
                Err("auto_provider_ended")
            } else {
                Ok(())
            };
        }
        let mut offset = 0;
        while offset < pcm.len() {
            if self.buffer.is_empty() {
                self.window_started = Some(deadline);
            }
            let take = (WINDOW_SAMPLES * 4 - self.buffer.len()).min(pcm.len() - offset);
            self.buffer.extend_from_slice(&pcm[offset..offset + take]);
            offset += take;
            if self.buffer.len() == WINDOW_SAMPLES * 4 {
                let sources = self
                    .decode(input, spool, self.window_started.unwrap())
                    .await?;
                self.publish(input, sources)?;
                self.buffer.clear();
            }
        }
        Ok(())
    }

    async fn decode(
        &mut self,
        input: &PcmInput,
        spool: &mut Spool,
        deadline: Instant,
    ) -> Result<Vec<AutoSource>, &'static str> {
        let samples = (self.buffer.len() / 4) as u64;
        let path = spool.write_window(&self.buffer)?;
        let Candidate::Whisper(client) = &mut self.candidate else {
            return Err("auto_provider_state");
        };
        input
            .session
            .auto_window(self.generation, self.epoch, true)?;
        let result = client
            .infer(
                &AudioChunk {
                    wav_path: path.to_string_lossy().into(),
                    start_sample: 0,
                    end_sample: samples,
                    sample_rate: 16000,
                },
                &self.context,
                deadline,
            )
            .await?;
        input
            .session
            .auto_window(self.generation, self.epoch, false)?;
        spool.clear()?;
        let mut sources = Vec::new();
        let duration = samples as f64 / 16000.;
        for segment in result.segments {
            let start = self.cursor * 1000 / 16000
                + ((segment.start_seconds.max(0.) * 1000.).round_ties_even() as u64)
                    .min(samples * 1000 / 16000);
            let end = self.cursor * 1000 / 16000
                + ((segment.end_seconds.max(0.) * 1000.).round_ties_even() as u64)
                    .min(samples * 1000 / 16000);
            if let Some(mapped) = self.clock.map(segment.text, start, end)? {
                sources.push(AutoSource {
                    mapped,
                    timestamp_basis: if segment.estimated_timestamps
                        || segment.start_seconds < 0.
                        || segment.end_seconds > duration
                    {
                        "sample_clock_estimated"
                    } else {
                        "sample_clock_native_hint"
                    },
                    chunk_start: Some(self.cursor),
                    chunk_end: self.cursor + samples,
                });
            }
        }
        self.cursor += samples;
        let mut state = input.state.lock().unwrap();
        // Replayed input is work, not another accepted source-clock interval.
        state.metrics.processed_samples = state
            .metrics
            .processed_samples
            .max(self.clock.origin_samples + self.cursor);
        state.metrics.decoded_windows += 1;
        state.metrics.empty_windows += u64::from(sources.is_empty());
        Ok(sources)
    }

    async fn failover(
        &mut self,
        input: &PcmInput,
        spool: &mut Spool,
        reason: FailoverReason,
    ) -> Result<(), &'static str> {
        if !matches!(self.candidate, Candidate::Stream(_)) || !self.options.whisper_ready {
            return Err("auto_candidates_exhausted");
        }
        self.epoch += 1;
        input
            .session
            .begin_auto_candidate(self.generation, self.epoch)?;
        self.candidate.stop().await?;
        spool.clear()?;
        if self.clock.sample_rate != 16000 {
            return Err("asr_sample_rate_mismatch");
        }
        let mut client = WhisperClient::spawn(
            &self.whisper_program,
            &self
                .whisper_args
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
            &self.sid,
            self.generation,
            &input.source,
            self.epoch,
            self.slot.clone(),
        )?;
        self.children.track(client.reaped.clone())?;
        if let Err(error) = client
            .prepare(Instant::now() + Duration::from_millis(self.options.startup_ms))
            .await
        {
            client.stop().await?;
            return Err(error);
        }
        self.candidate = Candidate::Whisper(client);
        let (origin, replay) = self.ring.snapshot();
        self.clock
            .candidate(16000, 1, origin, (replay.len() / 4) as u64)?;
        self.cursor = 0;
        self.buffer.clear();
        let deadline = Instant::now() + Duration::from_millis(self.options.startup_ms);
        let mut provisional = Vec::new();
        let mut bytes = 0;
        for part in replay.chunks(WINDOW_SAMPLES * 4) {
            self.buffer.extend_from_slice(part);
            if self.buffer.len() == WINDOW_SAMPLES * 4 {
                let sources = self.decode(input, spool, deadline).await?;
                bytes += sources.iter().map(|s| s.mapped.text.len()).sum::<usize>();
                provisional.extend(sources);
                if provisional.len() > 128 || bytes > 1024 * 1024 {
                    return Err("auto_replay_result_capacity");
                }
                self.buffer.clear();
            }
        }
        self.window_started = Some(Instant::now() + Duration::from_secs(120));
        input.session.activate_auto_candidate(
            self.generation,
            self.epoch,
            "faster-whisper",
            &self.clock,
            Some(reason),
        )?;
        if !provisional.is_empty() {
            self.publish(input, provisional)?;
        }
        Ok(())
    }

    async fn finish(&mut self, input: &PcmInput, spool: &mut Spool) -> Result<(), &'static str> {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            match &mut self.candidate {
                Candidate::Whisper(_) => {
                    if !self.buffer.is_empty() {
                        let sources = self.decode(input, spool, deadline).await?;
                        self.publish(input, sources)?;
                        self.buffer.clear();
                    }
                    return Ok(());
                }
                Candidate::Stream(client) => {
                    let batch = client.poll(true, deadline).await;
                    match batch {
                        Ok(batch) => {
                            let done = batch.done;
                            self.publish_stream(input, batch)?;
                            if done {
                                return Ok(());
                            }
                        }
                        Err(_) => {
                            self.failover(input, spool, FailoverReason::ProviderFailed)
                                .await?;
                        }
                    }
                }
            }
            if Instant::now() >= deadline {
                return Err("audio_drain_timeout");
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }
}
