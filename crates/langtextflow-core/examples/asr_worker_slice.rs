//! Explicit source-only prototype. No production backend/desktop cutover.
use langtextflow_core::asr_slice::{AsrSession, AudioChunk};
use langtextflow_core::supervision::{worker_slot, SupervisedWorker};

fn main() {
    if let Err(error) = run() {
        // No local paths or native exception bodies in the public receipt.
        eprintln!("{{\"status\":\"failed\",\"error\":\"{}\"}}", error);
        std::process::exit(1);
    }
}

fn run() -> Result<(), &'static str> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 7 {
        return Err(
            "usage: asr_worker_slice PYTHON WAV MODEL CACHE_DIR THREADS LANGUAGE FRAME_COUNT",
        );
    }
    let frames: u64 = args[6].parse().map_err(|_| "invalid_frame_count")?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|_| "runtime_start_failed")?;
    runtime.block_on(async {
        let session = AsrSession::default();
        session.start("asr-prototype")?;
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
                    .exchange(admitted.request(), &admitted.cancelled)
                    .await;
                let events = match reply {
                    Ok((next, reply)) => {
                        worker = Some(next);
                        session.finish(&admitted, Some(&reply))?
                    }
                    Err(_) => return session.finish(&admitted, None).map(|_| ()),
                };
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
        result
    })
}
