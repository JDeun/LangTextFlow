//! Explicit continuous-input prototype, never selected by production HTTP/desktop routes.
use langtextflow_core::asr_slice::AsrSession;
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::pcm_ingress::{PcmIngress, MAX_STREAM_SAMPLES};
use langtextflow_core::supervision::worker_slot;
use std::io::{Read, Write};
use std::time::{Duration, Instant};

fn main() {
    if let Err(error) = run() {
        eprintln!("{}", serde_json::json!({"status":"failed","error":error}));
        std::process::exit(1);
    }
}

fn run() -> Result<(), &'static str> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 9 && args.len() != 13 {
        return Err("usage: pcm_ingress_slice PYTHON PCM_F32LE MODEL CACHE THREADS LANGUAGE WS_PORT ORIGIN CREDENTIAL_FILE [MT_PYTHON MT_MODEL MT_TOKENIZER MT_THREADS]");
    }
    let mut source = std::fs::File::open(&args[1]).map_err(|_| "pcm_input_open")?;
    let metadata = source.metadata().map_err(|_| "pcm_input_metadata")?;
    let size = metadata.len();
    if !metadata.is_file() || size == 0 || !size.is_multiple_of(4) || size > MAX_STREAM_SAMPLES * 4
    {
        return Err("pcm_input_shape_or_capacity");
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "runtime_start_failed")?;
    runtime.block_on(async {
        let session = AsrSession::default();
        let targets = ["ko", "en", "ja"]
            .into_iter()
            .filter(|lang| *lang != args[5])
            .map(String::from)
            .collect::<Vec<_>>();
        let credential = session.start_observed("pcm-prototype", &args[5], &targets)?;
        let mut mt = None;
        if args.len() == 13 {
            let dispatcher = MtDispatcher::start(
                args[9].clone(),
                vec![
                    "-m".into(),
                    "langtextflow.translation.m2m100_worker".into(),
                    "--rust-protocol".into(),
                    args[10].clone(),
                    args[11].clone(),
                    args[12].clone(),
                ],
                4,
                worker_slot(),
            )?;
            session.enable_translation(
                &dispatcher,
                TranslationContext {
                    preset: "general".into(),
                    glossary: vec![],
                },
                30_000,
            )?;
            mt = Some(dispatcher);
        }
        let server = ObserverServer::bind(
            session.clone(),
            args[6].parse().map_err(|_| "invalid_port")?,
            &args[7],
        )
        .await?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut credential_file = options
            .open(&args[8])
            .map_err(|_| "credential_file_not_new")?;
        credential_file
            .write_all(
                serde_json::json!({
                    "url":format!("ws://{}{}",server.address(),credential.path()), "origin":args[7],
                    "scope":"continuous_pcm_rust_prototype",
                })
                .to_string()
                .as_bytes(),
            )
            .map_err(|_| "credential_write_failed")?;
        drop(credential_file);
        let owner = PcmIngress::start(
            session.clone(),
            &args[5],
            &args[0],
            &[
                "-m",
                "langtextflow.inference_worker",
                &args[2],
                &args[3],
                &args[4],
            ],
            worker_slot(),
            &std::env::temp_dir(),
        )?;
        let input = owner.input();
        let feed = async {
            let mut remaining = size;
            let mut frame = [0u8; 6400]; // 100ms transport frames; ingress owns 4s segmentation.
            while remaining > 0 {
                let count = remaining.min(frame.len() as u64) as usize;
                source
                    .read_exact(&mut frame[..count])
                    .map_err(|_| "pcm_input_truncated")?;
                input.feed(&frame[..count]).await?;
                remaining -= count as u64;
            }
            Ok::<_, &'static str>(())
        }
        .await;
        let result = match feed {
            Ok(()) => owner.finish().await,
            Err(error) => {
                owner.cancel().await?;
                Err(error)
            }
        };
        let deadline = Instant::now() + Duration::from_secs(31);
        let mut mt_drain = Ok(());
        while session.pending_translation_targets() > 0 {
            if Instant::now() >= deadline {
                mt_drain = Err("translation_drain_timeout");
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        if let Some(mt) = mt {
            mt.stop().await?;
        }
        server.stop().await?;
        let metrics = result?;
        mt_drain?;
        println!(
            "{}",
            serde_json::json!({
                "status":"input_drained", "scope":"rust_continuous_pcm_prototype",
                "metrics":metrics, "pcm16_quantized":true, "field_ready":false,
            })
        );
        Ok(())
    })
}
