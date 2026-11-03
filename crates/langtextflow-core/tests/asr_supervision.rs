use langtextflow_core::asr_slice::{AsrResult, AsrSession, AudioChunk, InferredSegment};
use langtextflow_core::supervision::{worker_slot, SupervisedWorker, WorkerError};
use langtextflow_core::{WorkerReply, WorkerStatus};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn python() -> String {
    std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python".into())
}
fn chunk() -> AudioChunk {
    AudioChunk {
        wav_path: "private.wav".into(),
        start_sample: 0,
        end_sample: 16_000,
        sample_rate: 16_000,
    }
}
fn reply(admitted: &langtextflow_core::asr_slice::Admission) -> WorkerReply {
    let request = admitted.request();
    WorkerReply {
        protocol_version: 1,
        request_id: request.request_id.clone(),
        session_id: request.session_id.clone(),
        session_generation: request.session_generation,
        segment_id: request.segment_id.clone(),
        source_revision: request.source_revision,
        status: WorkerStatus::Ok,
        error: None,
        result: Some(
            serde_json::to_string(&AsrResult {
                sample_count: 16_000,
                sample_rate: 16_000,
                segments: vec![InferredSegment {
                    start_seconds: 0.0,
                    end_seconds: 1.0,
                    text: "원문".into(),
                    estimated_timestamps: false,
                }],
            })
            .unwrap(),
        ),
    }
}

#[test]
fn rust_owns_bounded_admission_revision_generation_and_atomic_commit() {
    let session = AsrSession::default();
    session.start("s1").unwrap();
    let old = session.admit("seg", 1, "ko", chunk(), 100).unwrap();
    assert!(matches!(
        session.admit("other", 1, "ko", chunk(), 100),
        Err("busy")
    ));
    session.revise("seg", 2).unwrap();
    assert!(old.cancelled.load(Ordering::Acquire));
    assert!(matches!(
        session.finish(&old, Some(&reply(&old))),
        Err("cancelled")
    ));
    let current = session.admit("seg", 2, "ko", chunk(), 100).unwrap();
    let events = session.finish(&current, Some(&reply(&current))).unwrap();
    assert_eq!(events[0].text, "원문");
    assert_eq!(events[0].timestamp_basis, "sample_clock_native_hint");
    assert!(matches!(session.revise("seg", 3), Err("accepted_chunk")));
    assert!(matches!(session.finish(&old, None), Err("not_admitted")));
    let old_generation = session.admit("next", 1, "ko", chunk(), 100).unwrap();
    session.start("s2").unwrap();
    assert!(matches!(
        session.admit("new", 1, "ko", chunk(), 100),
        Err("busy")
    ));
    assert!(session
        .finish(&old_generation, Some(&reply(&old_generation)))
        .is_err());
    let new = session.admit("new", 1, "ko", chunk(), 100).unwrap();
    assert_eq!(
        session.finish(&new, Some(&reply(&new))).unwrap()[0].session_id,
        "s2"
    );
}

#[test]
fn malformed_or_out_of_bounds_native_result_cannot_commit() {
    let session = AsrSession::default();
    session.start("s").unwrap();
    for result in ["{}", "{\"sample_count\":16000,\"sample_rate\":16000,\"segments\":[{\"start_seconds\":0,\"end_seconds\":999,\"text\":\"bad\",\"estimated_timestamps\":false}]}"] {
        let admitted = session.admit("seg", 1, "ko", chunk(), 100).unwrap();
        let mut value = reply(&admitted); value.result = Some(result.into());
        assert!(matches!(session.finish(&admitted, Some(&value)), Err("invalid_result")));
    }
}

#[test]
fn small_decoder_padding_is_pcm_bounded_and_labeled_estimated() {
    let session = AsrSession::default();
    session.start("s").unwrap();
    let admitted = session.admit("seg", 1, "ko", chunk(), 100).unwrap();
    let mut value = reply(&admitted);
    let mut result: AsrResult = serde_json::from_str(value.result.as_ref().unwrap()).unwrap();
    result.segments[0].start_seconds = -0.1;
    result.segments[0].end_seconds = 1.2;
    value.result = Some(serde_json::to_string(&result).unwrap());
    let events = session.finish(&admitted, Some(&value)).unwrap();
    assert_eq!((events[0].start_ms, events[0].end_ms), (0, 1000));
    assert_eq!(events[0].timestamp_basis, "sample_clock_estimated");
}

#[test]
fn real_hung_child_deadline_reaps_before_replacement_and_no_spawn_queue() {
    runtime().block_on(async {
        let slot = worker_slot();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-c", "import time;time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        let pid = worker.pid().unwrap();
        assert!(matches!(
            SupervisedWorker::spawn(&python(), &["-c", "pass"], slot.clone()),
            Err(WorkerError::Busy)
        ));
        let session = AsrSession::default();
        session.start("s").unwrap();
        let admitted = session.admit("seg", 1, "ko", chunk(), 40).unwrap();
        let started = Instant::now();
        assert!(matches!(
            worker
                .exchange(admitted.request(), &admitted.cancelled)
                .await,
            Err(WorkerError::Deadline)
        ));
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(slot.available_permits(), 1);
        session.finish(&admitted, None).unwrap_err();
        let mut replacement = SupervisedWorker::spawn(
            &python(),
            &["-c", "import time;time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        assert_ne!(replacement.pid(), Some(pid));
        replacement.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn actual_child_cancellation_during_inference_kills_and_reaps() {
    runtime().block_on(async {
        let slot = worker_slot();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-c", "import sys,time;sys.stdin.readline();time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        let session = AsrSession::default();
        session.start("s").unwrap();
        let admitted = session.admit("seg", 1, "ko", chunk(), 10_000).unwrap();
        let owner = session.clone();
        let cancel = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(30)).await;
            owner.start("replacement").unwrap();
        });
        assert!(matches!(
            worker
                .exchange(admitted.request(), &admitted.cancelled)
                .await,
            Err(WorkerError::Cancelled)
        ));
        cancel.await.unwrap();
        assert!(session.finish(&admitted, None).is_err());
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn dropping_inflight_owner_retains_lease_until_actual_child_reaped() {
    runtime().block_on(async {
        let slot = worker_slot();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-c", "import time;time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        drop(worker);
        let deadline = Instant::now() + Duration::from_secs(2);
        while slot.available_permits() == 0 && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn real_worker_crash_or_oversized_output_is_terminal() {
    runtime().block_on(async {
        for code in [
            "raise SystemExit(3)",
            "import sys,time;sys.stdout.write('x'*131072);sys.stdout.flush();time.sleep(120)",
        ] {
            let slot = worker_slot();
            let worker = SupervisedWorker::spawn(&python(), &["-c", code], slot.clone()).unwrap();
            let session = AsrSession::default();
            session.start("s").unwrap();
            let admitted = session.admit("seg", 1, "ko", chunk(), 2000).unwrap();
            assert!(worker
                .exchange(admitted.request(), &AtomicBool::new(false))
                .await
                .is_err());
            assert_eq!(slot.available_permits(), 1);
        }
    });
}

#[test]
fn actual_jsonl_worker_result_requires_current_rust_fence() {
    runtime().block_on(async {
        let session = AsrSession::default();
        session.start("s").unwrap();
        let admitted = session.admit("seg", 1, "ko", chunk(), 2000).unwrap();
        let result = serde_json::to_string(&reply(&admitted)).unwrap();
        let code = format!(
            "import sys;sys.stdin.readline();print({result:?},flush=True);sys.stdin.readline()"
        );
        let worker = SupervisedWorker::spawn(&python(), &["-c", &code], worker_slot()).unwrap();
        let (mut worker, reply) = worker
            .exchange(admitted.request(), &AtomicBool::new(false))
            .await
            .unwrap();
        session.revise("seg", 2).unwrap();
        assert!(session.finish(&admitted, Some(&reply)).is_err());
        worker.stop().await.unwrap();
    });
}

#[test]
fn deadline_also_bounds_a_control_write_to_nonreading_child() {
    runtime().block_on(async {
        let session = AsrSession::default();
        session.start("s").unwrap();
        let admitted = session.admit("seg", 1, "ko", chunk(), 40).unwrap();
        let mut request = admitted.request().clone();
        request.payload = "x".repeat(100_000);
        let slot = worker_slot();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-c", "import time;time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        assert!(matches!(
            worker.exchange(&request, &AtomicBool::new(false)).await,
            Err(WorkerError::Deadline)
        ));
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn aborted_owner_cannot_leak_logical_admission_or_process_slot() {
    runtime().block_on(async {
        let session = AsrSession::default();
        session.start("s").unwrap();
        let owner = session.clone();
        let slot = worker_slot();
        let worker_slot = slot.clone();
        let task = tokio::spawn(async move {
            let admitted = owner.admit("seg", 1, "ko", chunk(), 10_000).unwrap();
            let worker = SupervisedWorker::spawn(
                &python(),
                &["-c", "import time;time.sleep(120)"],
                worker_slot,
            )
            .unwrap();
            let reply = worker
                .exchange(admitted.request(), &admitted.cancelled)
                .await;
            match reply {
                Ok((mut worker, reply)) => {
                    let _ = owner.finish(&admitted, Some(&reply));
                    worker.stop().await.unwrap();
                }
                Err(_) => {
                    let _ = owner.finish(&admitted, None);
                }
            }
        });
        while slot.available_permits() != 0 {
            tokio::task::yield_now().await;
        }
        task.abort();
        let _ = task.await;
        let deadline = Instant::now() + Duration::from_secs(2);
        while slot.available_permits() == 0 && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert_eq!(slot.available_permits(), 1);
        let next = session.admit("seg", 1, "ko", chunk(), 100).unwrap();
        assert_eq!(session.finish(&next, Some(&reply(&next))).unwrap().len(), 1);
    });
}

#[test]
fn external_timeout_consumes_worker_and_reaps_before_retry() {
    runtime().block_on(async {
        let slot = worker_slot();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-c", "import sys,time;sys.stdin.readline();time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        let session = AsrSession::default();
        session.start("s").unwrap();
        let admitted = session.admit("seg", 1, "ko", chunk(), 10_000).unwrap();
        assert!(tokio::time::timeout(
            Duration::from_millis(10),
            worker.exchange(admitted.request(), &admitted.cancelled)
        )
        .await
        .is_err());
        // `worker` has moved into the cancelled future and cannot be reused.
        let deadline = Instant::now() + Duration::from_secs(2);
        while slot.available_permits() == 0 && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert_eq!(slot.available_permits(), 1);
        assert!(session.finish(&admitted, None).is_err());
        let mut replacement = SupervisedWorker::spawn(
            &python(),
            &["-c", "import time;time.sleep(120)"],
            slot.clone(),
        )
        .unwrap();
        replacement.stop().await.unwrap();
    });
}

#[test]
fn drop_outside_or_after_tokio_runtime_still_reaps_before_release() {
    for close_runtime_first in [false, true] {
        let rt = runtime();
        let slot = worker_slot();
        let worker = rt.block_on(async {
            SupervisedWorker::spawn(
                &python(),
                &["-c", "import time;time.sleep(120)"],
                slot.clone(),
            )
            .unwrap()
        });
        if close_runtime_first {
            drop(rt);
            drop(worker);
        } else {
            drop(worker);
            drop(rt);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while slot.available_permits() == 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(slot.available_permits(), 1);
    }
}
