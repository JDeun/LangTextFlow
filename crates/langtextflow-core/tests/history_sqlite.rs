//! Real SQLite + Rust projection + real pure-inference OS children + actual WebSocket/Python readers.
use futures_util::StreamExt;
use langtextflow_core::asr_slice::{AsrSession, AudioChunk};
use langtextflow_core::history::HistoryWriter;
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::supervision::{worker_slot, SupervisedWorker};
use rusqlite::Connection;
use serde_json::Value;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio::sync::Semaphore;
use tokio_tungstenite::{client_async, tungstenite::client::IntoClientRequest};

// These immutable inference replies deliberately receive no database path/connection.
const ASR: &str = r#"
import json,sys
for line in sys.stdin:
    req=json.loads(line)
    result={'sample_count':16000,'sample_rate':16000,'segments':[{'start_seconds':0.,'end_seconds':1.,'text':sys.argv[1],'estimated_timestamps':False}]}
    reply={k:req[k] for k in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
    reply.update(status='ok',result=json.dumps(result),error=None)
    print(json.dumps(reply),flush=True)
"#;
const MT: &str = r#"
import json,sys,time
for line in sys.stdin:
    req=json.loads(line)
    if json.loads(req['payload'])['text'].startswith('REVISION'): time.sleep(1)
    failed=req['target_language']=='ko' and json.loads(req['payload'])['text']=='Original source 한글 日本語'
    reply={k:req[k] for k in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
    reply.update(status='error' if failed else 'ok',result=None if failed else 'Japanese target 日本語',error='synthetic failure' if failed else None)
    print(json.dumps(reply),flush=True)
"#;
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn python() -> String {
    std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python3".into())
}
#[path = "common/history.rs"]
mod fixture;
use fixture::Fixture;

#[test]
fn canonical_target_states_provenance_timestamps_reach_python_history_export_and_restore() {
    let f = Fixture::new("setup");
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("history-session", "en", &["ja".into(), "ko".into()])
            .unwrap();
        let mut writer =
            HistoryWriter::open(f.db(), "history-session", 1, 8, Arc::new(Semaphore::new(1)))
                .await
                .unwrap();
        session.attach_history(writer.sink()).unwrap();
        let dispatcher = MtDispatcher::start(
            python(),
            vec!["-u".into(), "-c".into(), MT.into()],
            4,
            worker_slot(),
        )
        .unwrap();
        session
            .enable_translation(
                &dispatcher,
                TranslationContext {
                    preset: "general".into(),
                    glossary: vec![],
                },
                5000,
            )
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, "http://127.0.0.1:5185")
            .await
            .unwrap();
        let mut request = format!("ws://{}{}", server.address(), token.path())
            .into_client_request()
            .unwrap();
        request
            .headers_mut()
            .insert("Origin", "http://127.0.0.1:5185".parse().unwrap());
        let (mut socket, _) =
            client_async(request, TcpStream::connect(server.address()).await.unwrap())
                .await
                .unwrap();
        let _ = socket.next().await.unwrap().unwrap();
        let worker = SupervisedWorker::spawn(
            &python(),
            &["-u", "-c", ASR, "Original source 한글 日本語"],
            worker_slot(),
        )
        .unwrap();
        let admitted = session
            .admit(
                "chunk",
                1,
                "en",
                AudioChunk {
                    wav_path: "synthetic.wav".into(),
                    start_sample: 0,
                    end_sample: 16000,
                    sample_rate: 16000,
                },
                5000,
            )
            .unwrap();
        let (mut worker, reply) = worker
            .exchange(admitted.request(), &admitted.cancelled, admitted.deadline())
            .await
            .unwrap();
        session.finish(&admitted, Some(&reply)).unwrap();
        let final_event = tokio::time::timeout(Duration::from_secs(8), async {
            loop {
                let event: Value =
                    serde_json::from_str(socket.next().await.unwrap().unwrap().to_text().unwrap())
                        .unwrap();
                if event["committed"] == true {
                    break event;
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(final_event["translation_states"]["ko"], "failed");
        assert_eq!(final_event["translation_states"]["ja"], "available");
        std::fs::write(
            f.0.join("observed.json"),
            serde_json::to_vec(&final_event).unwrap(),
        )
        .unwrap();
        assert!(writer.flush().await.unwrap().complete);
        dispatcher.stop().await.unwrap();
        worker.stop().await.unwrap();
        session.stop(); // Captures stop timestamp before disk drain, fences old scope.
        let report = writer.stop().await.unwrap();
        assert!(report.complete && report.stopped);
        assert_eq!(report.accepted, report.persisted);
        server.stop().await.unwrap();
        f.python("verify");
    });
}

#[test]
fn locked_disk_and_overflow_never_block_real_source_mt_or_fast_observer() {
    for write_failure in [false, true] {
        exercise_history_failure(write_failure);
    }
}

fn exercise_history_failure(write_failure: bool) {
    let f = Fixture::new("setup");
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("history-session", "en", &["ja".into(), "ko".into()])
            .unwrap();
        let mut writer =
            HistoryWriter::open(f.db(), "history-session", 1, 1, Arc::new(Semaphore::new(1)))
                .await
                .unwrap();
        session.attach_history(writer.sink()).unwrap();
        let connection = Connection::open(f.db()).unwrap();
        if write_failure {
            connection
                .execute_batch(
                    "CREATE TRIGGER fail_history BEFORE INSERT ON transcript_segments
                BEGIN SELECT RAISE(FAIL,'private-path-and-credential'); END",
                )
                .unwrap();
        } else {
            connection.execute_batch("BEGIN IMMEDIATE").unwrap();
        }
        let dispatcher = MtDispatcher::start(
            python(),
            vec!["-u".into(), "-c".into(), MT.into()],
            16,
            worker_slot(),
        )
        .unwrap();
        session
            .enable_translation(
                &dispatcher,
                TranslationContext {
                    preset: "general".into(),
                    glossary: vec![],
                },
                5000,
            )
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, "http://127.0.0.1:5185")
            .await
            .unwrap();
        let mut request = format!("ws://{}{}", server.address(), token.path())
            .into_client_request()
            .unwrap();
        request
            .headers_mut()
            .insert("Origin", "http://127.0.0.1:5185".parse().unwrap());
        let (mut socket, _) =
            client_async(request, TcpStream::connect(server.address()).await.unwrap())
                .await
                .unwrap();
        let _ = socket.next().await;
        let received = tokio::spawn(async move {
            let mut source = std::collections::HashSet::new();
            let mut committed = std::collections::HashSet::new();
            let mut available = false;
            while let Some(Ok(frame)) = socket.next().await {
                let event: Value = serde_json::from_str(frame.to_text().unwrap()).unwrap();
                if event["type"] == "transcript" {
                    available |= event["translation_states"]["ja"] == "available";
                    source.insert(event["segment_id"].as_str().unwrap().to_owned());
                    if event["committed"] == true {
                        committed.insert(event["segment_id"].as_str().unwrap().to_owned());
                    }
                    if committed.len() == 12 {
                        break;
                    }
                }
            }
            (source.len(), committed.len(), available)
        });
        let mut worker = Some(
            SupervisedWorker::spawn(&python(), &["-u", "-c", ASR, "progress"], worker_slot())
                .unwrap(),
        );
        let started = Instant::now();
        for index in 0..12 {
            let admitted = session
                .admit(
                    &format!("chunk-{index}"),
                    1,
                    "en",
                    AudioChunk {
                        wav_path: "synthetic.wav".into(),
                        start_sample: index * 16000,
                        end_sample: (index + 1) * 16000,
                        sample_rate: 16000,
                    },
                    5000,
                )
                .unwrap();
            let (next, reply) = worker
                .take()
                .unwrap()
                .exchange(admitted.request(), &admitted.cancelled, admitted.deadline())
                .await
                .unwrap();
            worker = Some(next);
            session.finish(&admitted, Some(&reply)).unwrap();
            tokio::task::yield_now().await;
        }
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(10), received)
                .await
                .unwrap()
                .unwrap(),
            (12, 12, true)
        );
        assert!(started.elapsed() < Duration::from_secs(10));
        let report = writer.sink().metrics();
        assert!(!report.complete && (report.dropped > 0 || report.write_errors > 0));
        if !write_failure {
            connection.execute_batch("ROLLBACK").unwrap();
        }
        dispatcher.stop().await.unwrap();
        worker.unwrap().stop().await.unwrap();
        session.stop();
        let ended = Instant::now();
        let stopped = writer.stop().await.unwrap();
        assert!(!stopped.complete);
        if write_failure {
            assert_eq!(stopped.error, Some("history_write_failed"));
        }
        assert!(ended.elapsed() < Duration::from_secs(4));
        server.stop().await.unwrap();
    });
}

#[test]
fn legacy_schema_requires_existing_migration_and_does_not_create_a_parallel_store() {
    let f = Fixture::new("legacy");
    runtime().block_on(async {
        let slot = Arc::new(Semaphore::new(1));
        assert!(
            HistoryWriter::open(f.db(), "history-session", 1, 2, slot.clone())
                .await
                .is_err()
        );
        f.python("migrate");
        let mut writer = HistoryWriter::open(f.db(), "history-session", 1, 2, slot)
            .await
            .unwrap();
        assert!(writer.stop().await.unwrap().complete);
        assert!(HistoryWriter::open(
            f.0.join("absent.db"),
            "history-session",
            1,
            2,
            Arc::new(Semaphore::new(1))
        )
        .await
        .is_err());
        assert!(!f.0.join("absent.db").exists());
    });
}

#[test]
fn reset_closes_old_writer_scope_and_rejects_old_reply_without_rewriting_session() {
    let f = Fixture::new("setup");
    runtime().block_on(async {
        let session = AsrSession::default();
        session.start_observed("history-session", "en", &["ja".into(), "ko".into()]).unwrap();
        let mut writer = HistoryWriter::open(f.db(), "history-session", 1, 4,
            Arc::new(Semaphore::new(1))).await.unwrap();
        let old_sink = writer.sink();
        session.attach_history(old_sink.clone()).unwrap();
        let admitted = session.admit("old", 1, "en", AudioChunk { wav_path: "synthetic.wav".into(),
            start_sample: 0, end_sample: 16000, sample_rate: 16000 }, 5000).unwrap();
        let request = admitted.request();
        let reply = langtextflow_core::WorkerReply { protocol_version: 1,
            request_id: request.request_id.clone(), session_id: request.session_id.clone(),
            session_generation: request.session_generation, segment_id: request.segment_id.clone(),
            source_revision: 1, status: langtextflow_core::WorkerStatus::Ok, error: None,
            result: Some(serde_json::json!({"sample_count":16000,"sample_rate":16000,"segments":[
                {"start_seconds":0.,"end_seconds":1.,"text":"late old speech","estimated_timestamps":false}
            ]}).to_string()) };
        session.start_observed("new-session", "en", &["ja".into(), "ko".into()]).unwrap();
        assert_eq!(session.attach_history(old_sink).err(), Some("history_scope"));
        assert!(session.finish(&admitted, Some(&reply)).is_err());
        let report = writer.stop().await.unwrap();
        assert!(report.complete && report.stopped && report.accepted == 0);
        let connection = Connection::open(f.db()).unwrap();
        let count: i64 = connection.query_row("SELECT COUNT(*) FROM transcript_segments", [], |r|r.get(0)).unwrap();
        let ended: String = connection.query_row("SELECT ended_at FROM sessions WHERE session_id='history-session'",[],|r|r.get(0)).unwrap();
        assert_eq!(count,0);
        assert!(!ended.is_empty());
        session.stop();
    });
}

#[test]
fn revised_two_to_one_to_empty_removes_obsolete_uncommitted_history() {
    let f = Fixture::new("setup");
    runtime().block_on(async {
        let session = AsrSession::default();
        session.start_observed("history-session", "en", &["ja".into(), "ko".into()]).unwrap();
        let mut writer = HistoryWriter::open(f.db(), "history-session", 1, 16,
            Arc::new(Semaphore::new(1))).await.unwrap();
        session.attach_history(writer.sink()).unwrap();
        let slot = worker_slot();
        let dispatcher = MtDispatcher::start(python(),vec!["-u".into(),"-c".into(),MT.into()],8,slot).unwrap();
        session.enable_translation(&dispatcher,TranslationContext {preset:"general".into(),glossary:vec![]},5000).unwrap();
        for (revision,count) in [(1,2),(2,1),(3,0)] {
            if revision>1 {session.revise("chunk",revision).unwrap();}
            let admitted = session.admit("chunk",revision,"en",AudioChunk {wav_path:"synthetic.wav".into(),
                start_sample:0,end_sample:16000,sample_rate:16000},5000).unwrap();
            let request=admitted.request();
            let segments=(0..count).map(|index|serde_json::json!({"start_seconds":index as f64/2.,
                "end_seconds":(index+1) as f64/2.,"text":format!("REVISION-source-{revision}-{index}"),"estimated_timestamps":false})).collect::<Vec<_>>();
            let reply=langtextflow_core::WorkerReply {protocol_version:1,request_id:request.request_id.clone(),
                session_id:request.session_id.clone(),session_generation:request.session_generation,
                segment_id:request.segment_id.clone(),source_revision:revision,status:langtextflow_core::WorkerStatus::Ok,
                error:None,result:Some(serde_json::json!({"sample_count":16000,"sample_rate":16000,"segments":segments}).to_string())};
            session.finish(&admitted,Some(&reply)).unwrap();
            writer.flush().await.unwrap();
            let connection=Connection::open(f.db()).unwrap();
            let stored:i64=connection.query_row("SELECT COUNT(*) FROM transcript_segments",[],|r|r.get(0)).unwrap();
            assert_eq!(stored,count);
        }
        assert_eq!(writer.sink().metrics().invalidated,3);
        dispatcher.stop().await.unwrap();
        session.stop();
        assert!(writer.stop().await.unwrap().complete);
    });
}
