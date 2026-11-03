//! Explicit source-only prototype. No production backend/desktop cutover.
use langtextflow_core::asr_slice::{AsrSession, AudioChunk};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::supervision::{worker_slot, SupervisedWorker};
use std::io::Write;

fn main() {
    if let Err(error) = run() {
        // No local paths or native exception bodies in the public receipt.
        eprintln!("{{\"status\":\"failed\",\"error\":\"{}\"}}", error);
        std::process::exit(1);
    }
}

fn run() -> Result<(), &'static str> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 7 && args.len() != 10 {
        return Err(
            "usage: asr_worker_slice PYTHON WAV MODEL CACHE THREADS LANGUAGE FRAMES [WS_PORT ORIGIN CREDENTIAL_FILE]",
        );
    }
    let frames: u64 = args[6].parse().map_err(|_| "invalid_frame_count")?;
    if frames == 0 || frames > 128 * 4 * 16_000 {
        return Err("invalid_frame_count");
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "runtime_start_failed")?;
    runtime.block_on(async {
        let session = AsrSession::default();
        let observer = if args.len() == 10 {
            let targets = ["ko", "en", "ja"]
                .into_iter()
                .filter(|lang| *lang != args[5])
                .map(String::from)
                .collect::<Vec<_>>();
            let token = session.start_observed("asr-prototype", &args[5], &targets)?;
            let server = ObserverServer::bind(
                session.clone(),
                args[7].parse().map_err(|_| "invalid_port")?,
                &args[8],
            )
            .await?;
            let mut options = std::fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options
                .open(&args[9])
                .map_err(|_| "credential_file_not_new")?;
            let data = serde_json::json!({"url":format!("ws://{}{}",server.address(),token.path()),
                "origin":args[8],"scope":"source_only_rust_prototype"});
            file.write_all(data.to_string().as_bytes())
                .map_err(|_| "credential_write_failed")?;
            Some(server)
        } else {
            session.start("asr-prototype")?;
            None
        };
        let worker = SupervisedWorker::spawn(
            &args[0],
            &[
                "-m",
                "langtextflow.inference_worker",
                &args[2],
                &args[3],
                &args[4],
            ],
            worker_slot(),
        )
        .map_err(|_| "worker_spawn_failed")?;
        let mut worker = Some(worker);
        let mut start = 0;
        let mut sequence = 0;
        let result = async {
            while start < frames {
                let end = (start + 4 * 16_000).min(frames);
                sequence += 1;
                let admitted = session.admit(
                    &format!("pcm-{sequence}"),
                    1,
                    &args[5],
                    AudioChunk {
                        wav_path: args[1].clone(),
                        start_sample: start,
                        end_sample: end,
                        sample_rate: 16_000,
                    },
                    120_000,
                )?;
                let reply = worker
                    .take()
                    .ok_or("worker_unavailable")?
                    .exchange(admitted.request(), &admitted.cancelled, admitted.deadline())
                    .await;
                let events = match reply {
                    Ok((next, reply)) => {
                        worker = Some(next);
                        session.finish(&admitted, Some(&reply))?
                    }
                    Err(_) => return session.finish(&admitted, None).map(|_| ()),
                };
                // Observer transport makes progress between native windows;
                // source publication never waits for a socket.
                tokio::task::yield_now().await;
                println!(
                    "{}",
                    serde_json::json!({"status":"accepted", "scope":"rust_asr_prototype",
                    "field_ready":false, "events":events})
                );
                start = end;
            }
            Ok(())
        }
        .await;
        if let Some(mut worker) = worker {
            worker.stop().await.map_err(|_| "worker_cleanup_failed")?;
        }
        if let Some(observer) = observer {
            observer.stop().await?;
        }
        result
    })
}
