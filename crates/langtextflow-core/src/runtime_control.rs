//! Private owned-parent control transport; the existing observer owns caption output.
use crate::asr_slice::AsrSession;
use crate::history::HistoryWriter;
use crate::mt_slice::MtDispatcher;
use crate::observer_ws::ObserverServer;
use crate::pcm_ingress::{PcmIngress, FRAME_LIMIT};
use crate::source_context::SourceContext;
use crate::supervision::worker_slot;
use futures_util::future::{select, Either};
use serde::Deserialize;
use serde_json::json;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, oneshot, watch, Semaphore};

pub const ORIGIN: &str = "http://127.0.0.1";
const CONFIG_LIMIT: usize = 64 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Worker {
    program: String,
    args: Vec<String>,
}

impl Worker {
    fn valid(&self) -> bool {
        !self.program.is_empty()
            && self.program.len() <= 4096
            && self.args.len() <= 16
            && self.args.iter().all(|arg| arg.len() <= 4096)
            && !self.program.contains('\0')
            && self.args.iter().all(|arg| !arg.contains('\0'))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    protocol_version: u16,
    session_id: String,
    source_language: String,
    target_languages: Vec<String>,
    database_path: String,
    spool_directory: PathBuf,
    asr: Worker,
    #[serde(default)]
    asr_model_identity: Option<String>,
    mt: Option<Worker>,
    translation_deadline_ms: u64,
    #[serde(default)]
    context: SourceContext,
}

enum Command {
    Audio(Vec<u8>),
    End,
    Stop,
}

/// A full command is read and size-checked before admission. A second queued
/// command is a protocol error, not a new waiting queue. EOF independently
/// interrupts even a blocked feed/native drain.
fn read_commands(
    mut reader: impl Read,
    sender: mpsc::Sender<Command>,
    closed: watch::Sender<bool>,
) {
    let mut read = || -> Result<(), ()> {
        loop {
            let mut header = [0_u8; 5];
            reader.read_exact(&mut header).map_err(|_| ())?;
            let size = u32::from_le_bytes(header[1..].try_into().unwrap()) as usize;
            let command = match (header[0], size) {
                (1, 1..=FRAME_LIMIT) if size.is_multiple_of(4) => {
                    let mut frame = vec![0; size];
                    reader.read_exact(&mut frame).map_err(|_| ())?;
                    Command::Audio(frame)
                }
                (2, 0) => Command::End,
                (3, 0) => Command::Stop,
                _ => return Err(()),
            };
            sender.try_send(command).map_err(|_| ())?;
        }
    };
    let _ = read();
    let _ = closed.send(true);
}

type Output = (String, oneshot::Sender<Result<(), &'static str>>);

async fn write_frame(
    output: &std::sync::mpsc::SyncSender<Output>,
    value: serde_json::Value,
) -> Result<(), &'static str> {
    let frame = value.to_string();
    if frame.len() > 1024 {
        return Err("control_reply_limit");
    }
    let (tx, rx) = oneshot::channel();
    output.try_send((frame, tx)).map_err(|_| "control_output")?;
    tokio::time::timeout(Duration::from_secs(1), rx)
        .await
        .map_err(|_| "control_output_timeout")?
        .map_err(|_| "control_output")?
}

async fn confirm_audio_cleanup(slot: &Arc<Semaphore>, root: Option<&std::path::Path>) -> bool {
    let deadline = Instant::now() + Duration::from_secs(3);
    while (slot.available_permits() == 0 || root.is_some_and(|root| root.exists()))
        && Instant::now() < deadline
    {
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    slot.available_permits() > 0 && root.is_none_or(|root| !root.exists())
}

pub fn run_stdio() -> Result<(), &'static str> {
    let mut reader = BufReader::with_capacity(CONFIG_LIMIT + 1, std::io::stdin());
    let mut raw = Vec::new();
    reader
        .by_ref()
        .take((CONFIG_LIMIT + 1) as u64)
        .read_until(b'\n', &mut raw)
        .map_err(|_| "control_config")?;
    if raw.len() > CONFIG_LIMIT || raw.last() != Some(&b'\n') {
        return Err("control_config_limit");
    }
    let config: Config = serde_json::from_slice(&raw).map_err(|_| "control_config")?;
    if config.protocol_version != 1
        || config.session_id.is_empty()
        || config.session_id.len() > 64
        || !config.asr.valid()
        || config.mt.as_ref().is_some_and(|mt| !mt.valid())
        || config.database_path.is_empty()
        || config.database_path.len() > 4096
        || config.spool_directory.as_os_str().len() > 4096
        || !(1..=120_000).contains(&config.translation_deadline_ms)
    {
        return Err("control_config");
    }
    let (tx, rx) = mpsc::channel(1);
    let (closed_tx, closed) = watch::channel(false);
    std::thread::spawn(move || read_commands(reader, tx, closed_tx));
    let (output, frames) = std::sync::mpsc::sync_channel::<Output>(1);
    // A full OS pipe blocks only this process-owned std thread. Async timeout
    // still reaps inference; process exit ends a nonreading parent's writer.
    std::thread::spawn(move || {
        let mut stdout = std::io::stdout().lock();
        while let Ok((frame, ack)) = frames.recv() {
            let result = writeln!(stdout, "{frame}")
                .and_then(|_| stdout.flush())
                .map_err(|_| "control_output");
            let failed = result.is_err();
            let _ = ack.send(result);
            if failed {
                break;
            }
        }
    });
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "runtime_start")?
        .block_on(serve(config, rx, closed, output))
}

async fn serve(
    config: Config,
    mut commands: mpsc::Receiver<Command>,
    mut parent_closed: watch::Receiver<bool>,
    output: std::sync::mpsc::SyncSender<Output>,
) -> Result<(), &'static str> {
    let session = AsrSession::default();
    let credential = session.start_observed(
        &config.session_id,
        &config.source_language,
        &config.target_languages,
    )?;
    let generation = session.audio_generation(&config.source_language)?;
    session.configured_asr_model(config.asr_model_identity)?;
    session.configure_context(&config.source_language, config.context.clone())?;
    let mut history = HistoryWriter::open(
        &config.database_path,
        &config.session_id,
        generation,
        32,
        Arc::new(Semaphore::new(1)),
    )
    .await
    .ok();
    let history_attached = history
        .as_ref()
        .is_some_and(|writer| session.attach_history(writer.sink()).is_ok());
    let mt = if let Some(worker) = config.mt {
        let dispatcher = MtDispatcher::start(worker.program, worker.args, 4, worker_slot())?;
        session.enable_translation(
            &dispatcher,
            config.context.translation(),
            config.translation_deadline_ms,
        )?;
        Some(dispatcher)
    } else {
        None
    };
    let observer = ObserverServer::bind(session.clone(), 0, ORIGIN).await?;
    let asr_slot = worker_slot();
    let started = PcmIngress::start(
        session.clone(),
        &config.source_language,
        &config.asr.program,
        &config
            .asr
            .args
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        asr_slot.clone(),
        &config.spool_directory,
    );
    let mut ingress = match started {
        Ok(ingress) => Some(ingress),
        Err(error) => {
            // Spool creation can fail after spawn; await the independent reaper.
            let reaped = confirm_audio_cleanup(&asr_slot, None).await;
            if let Some(mt) = mt {
                let _ = mt.stop().await;
            }
            if let Some(writer) = &mut history {
                let _ = writer.stop().await;
            }
            let _ = observer.stop().await;
            return Err(if reaped {
                error
            } else {
                "audio_cleanup_unconfirmed"
            });
        }
    };
    let input = ingress.as_ref().unwrap().input();
    let cleanup_path = ingress.as_ref().unwrap().cleanup_path().to_owned();
    let mut failure = write_frame(
        &output,
        json!({"type":"ready", "protocol_version":1,
        "session_id":config.session_id,"session_generation":generation,
        "port":observer.address().port(), "observer_path":credential.path(),
        "history_available":history_attached}),
    )
    .await
    .err();

    let mut sequence = 0_u64;
    loop {
        if *parent_closed.borrow() || failure.is_some() {
            break;
        }
        let command =
            match select(Box::pin(commands.recv()), Box::pin(parent_closed.changed())).await {
                Either::Left((Some(command), _)) => command,
                _ => break,
            };
        sequence += 1;
        let stop = matches!(command, Command::Stop);
        let operation = async {
            match command {
                Command::Audio(frame) => input.feed(&frame).await?,
                Command::End | Command::Stop => {
                    if let Some(owner) = ingress.take() {
                        owner.finish().await?;
                    }
                    let deadline = Instant::now()
                        + Duration::from_millis(config.translation_deadline_ms + 250);
                    while session.pending_translation_targets() > 0 {
                        if Instant::now() >= deadline {
                            return Err("translation_drain_timeout");
                        }
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                }
            }
            Ok::<_, &'static str>(())
        };
        let result = match select(Box::pin(operation), Box::pin(parent_closed.changed())).await {
            Either::Left((result, _)) => result,
            Either::Right(_) => break,
        };
        if let Err(error) = result {
            failure = Some(error);
            break;
        }
        if let Err(error) = write_frame(
            &output,
            json!({"type":"ack", "session_id":config.session_id,
            "session_generation":generation,"sequence":sequence,
            "serial":session.publication_serial(),
            "history":history.as_ref().filter(|_|history_attached).map(|writer| writer.sink().metrics())}),
        )
        .await
        {
            failure = Some(error);
            break;
        }
        if stop {
            break;
        }
    }
    // Revoke on parent loss, but retain accepted final results for explicit stop.
    if *parent_closed.borrow() || failure.is_some() {
        session.stop();
    }
    if let Some(owner) = ingress.take() {
        let _ = owner.cancel().await;
    }
    if let Some(dispatcher) = mt {
        if let Err(error) = dispatcher.stop().await {
            failure.get_or_insert(error);
        }
    }
    if !confirm_audio_cleanup(&asr_slot, Some(&cleanup_path)).await {
        failure.get_or_insert("audio_cleanup_unconfirmed");
    }
    if let Some(writer) = &mut history {
        if writer.stop().await.is_err() {
            failure.get_or_insert("history_stop_unconfirmed");
        }
    }
    let history_metrics = history
        .as_ref()
        .filter(|_| history_attached)
        .map(|writer| writer.sink().metrics());
    let _ = observer.stop().await;
    session.stop();
    if *parent_closed.borrow() {
        return failure.map_or(Ok(()), Err);
    }
    write_frame(
        &output,
        json!({"type":"stopped", "session_id":config.session_id,
        "session_generation":generation,"history":history_metrics,
        "error":failure}),
    )
    .await?;
    failure.map_or(Ok(()), Err)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn control_output_without_a_reader_has_a_bounded_async_deadline() {
        let (output, _unread) = std::sync::mpsc::sync_channel(1);
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let start = Instant::now();
        assert_eq!(
            runtime.block_on(write_frame(&output, json!({"type":"ack"}))),
            Err("control_output_timeout")
        );
        assert!(start.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn invalid_declared_frame_fails_before_reading_or_allocating_its_payload() {
        let (tx, mut rx) = mpsc::channel(1);
        let (closed, observed) = watch::channel(false);
        let mut header = vec![1];
        header.extend_from_slice(&u32::MAX.to_le_bytes());
        read_commands(std::io::Cursor::new(header), tx, closed);
        assert!(*observed.borrow() && rx.try_recv().is_err());
    }
}
