//! Actual OS children and WebSockets; synthetic inference, no model/quality claim.
use futures_util::StreamExt;
use langtextflow_core::asr_slice::{AsrResult, AsrSession, AudioChunk, InferredSegment};
use langtextflow_core::caption_events::ObserverCredential;
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::supervision::worker_slot;
use langtextflow_core::{WorkerReply, WorkerStatus};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio_tungstenite::{client_async, tungstenite::client::IntoClientRequest, WebSocketStream};

const ORIGIN: &str = "http://127.0.0.1:5185";
const CHILD: &str = r#"
import json, os, sys, time
path = sys.argv[1]
with open(path, 'a') as log: log.write(json.dumps({'pid':os.getpid()})+'\n')
for line in sys.stdin:
    req = json.loads(line)
    text = json.loads(req['payload'])['text']
    target = req['target_language']
    with open(path, 'a') as log: log.write(json.dumps({'text':text,'target':target,'pid':os.getpid()})+'\n')
    if text == 'HANG' and target == 'en': time.sleep(60)
    if text == 'SLOW': time.sleep(.2)
    if text == 'QUEUED-SLOW': time.sleep(.2)
    if text == 'REVISION': time.sleep(.4)
    reply = {key:req[key] for key in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
    failed = text == 'FAIL' and target == 'ko'
    reply.update(status='error' if failed else 'ok',result=None if failed else 'translated-'+target,error='private-fixture-error' if failed else None)
    if text.startswith('2010') and target == 'en':
        reply.update(status='error',result=None,error='translation_rejected')
        if text == '2010-WRONG': reply['request_id'] = 'wrong'
        if text == '2010-EXTRA': reply['extra'] = 'untrusted'
        if text == '2010-UNKNOWN': reply['error'] = 'translation_unknown'
        if text == '2010-RESULT': reply['result'] = 'must-not-publish'
    if text == 'WRONG': reply['request_id'] = 'wrong'
    print(json.dumps(reply), flush=True)
    if failed: sys.exit(1)
"#;
type Client = WebSocketStream<TcpStream>;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "ltf-mt-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn log(&self) -> PathBuf {
        self.0.join("child.jsonl")
    }
    fn records(&self) -> Vec<Value> {
        std::fs::read_to_string(self.log())
            .unwrap_or_default()
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    }
    fn dispatcher(
        &self,
        capacity: usize,
        slot: std::sync::Arc<tokio::sync::Semaphore>,
    ) -> MtDispatcher {
        MtDispatcher::start(
            std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python3".into()),
            vec![
                "-u".into(),
                "-c".into(),
                CHILD.into(),
                self.log().to_string_lossy().into(),
            ],
            capacity,
            slot,
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn enable(session: &AsrSession, dispatcher: &MtDispatcher, deadline: u64) {
    session
        .enable_translation(
            dispatcher,
            TranslationContext {
                preset: "general".into(),
                glossary: vec![],
            },
            deadline,
        )
        .unwrap();
}
fn publish(session: &AsrSession, id: &str, revision: u64, source: &str, text: &str) {
    publish_segments(session, id, revision, source, &[text]);
}
fn publish_segments(session: &AsrSession, id: &str, revision: u64, source: &str, texts: &[&str]) {
    let admitted = session
        .admit(
            id,
            revision,
            source,
            AudioChunk {
                wav_path: "synthetic.wav".into(),
                start_sample: 0,
                end_sample: 16_000,
                sample_rate: 16_000,
            },
            2000,
        )
        .unwrap();
    let request = admitted.request();
    let reply = WorkerReply {
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
                segments: texts
                    .iter()
                    .enumerate()
                    .map(|(index, text)| InferredSegment {
                        start_seconds: index as f64 / texts.len() as f64,
                        end_seconds: (index + 1) as f64 / texts.len() as f64,
                        text: (*text).into(),
                        estimated_timestamps: false,
                    })
                    .collect(),
            })
            .unwrap(),
        ),
    };
    session.finish(&admitted, Some(&reply)).unwrap();
}
async fn connect(server: &ObserverServer, token: &ObserverCredential) -> Client {
    let mut request = format!("ws://{}{}", server.address(), token.path())
        .into_client_request()
        .unwrap();
    request
        .headers_mut()
        .insert("Origin", ORIGIN.parse().unwrap());
    client_async(request, TcpStream::connect(server.address()).await.unwrap())
        .await
        .unwrap()
        .0
}
async fn receive(client: &mut Client) -> Value {
    let frame = tokio::time::timeout(Duration::from_secs(3), client.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    serde_json::from_str(frame.to_text().unwrap()).unwrap()
}
async fn until_commit(client: &mut Client, id: &str) -> Vec<Value> {
    let mut frames = Vec::new();
    loop {
        let frame = receive(client).await;
        let done = frame["segment_id"] == id && frame["committed"] == true;
        frames.push(frame);
        if done {
            return frames;
        }
    }
}
async fn wait_inference(fixture: &Fixture, text: &str) {
    tokio::time::timeout(Duration::from_secs(2), async {
        while !fixture.records().iter().any(|row| row["text"] == text) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}

#[test]
fn independent_target_failure_preserves_source_good_target_and_reconnect() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("mixed", "en", &["ja".into(), "ko".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "chunk", 1, "en", "FAIL");
        let frames = until_commit(&mut socket, "rs-1-0").await;
        assert_eq!(frames.len(), 3);
        for (index, frame) in frames.iter().enumerate() {
            assert_eq!(frame["version"], index + 1);
            assert_eq!(frame["original_text"], "FAIL");
            assert_eq!(frame["text"], "FAIL");
        }
        assert_eq!(
            frames[0]["translation_states"],
            json!({"ja":"pending","ko":"pending"})
        );
        assert_eq!(
            frames[1]["translation_states"],
            json!({"ja":"available","ko":"pending"})
        );
        assert_eq!(
            frames[2]["translation_states"],
            json!({"ja":"available","ko":"failed"})
        );
        assert_eq!(frames[2]["translations"], json!({"ja":"translated-ja"}));
        assert_eq!(
            frames[2]["translation_provenance"]["ja"]["provider"],
            "m2m100-ct2"
        );
        assert!(frames[2]["translation_provenance"]["ja"]["verified_model"].is_null());
        assert!(session.revise("chunk", 2).is_err());
        let mut restored = connect(&server, &token).await;
        assert_eq!(receive(&mut restored).await["segments"][0], frames[2]);
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn request_rejection_preserves_source_peer_target_and_healthy_worker_pid() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session.start_observed("quality", "ko", &["en".into(), "ja".into()]).unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN).await.unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "numeric", 1, "ko", "2010...");
        let frames = until_commit(&mut socket, "rs-1-0").await;
        let rejected = frames.last().unwrap();
        assert_eq!(rejected["original_text"], "2010...");
        assert_eq!(rejected["text"], "2010...");
        assert_eq!(rejected["translation_states"], json!({"en":"failed", "ja":"available"}));
        assert_eq!(rejected["translations"], json!({"ja":"translated-ja"}));
        assert!(rejected["translation_provenance"].get("en").is_none());
        publish(&session, "healthy", 1, "ko", "healthy");
        let frames = until_commit(&mut socket, "rs-2-0").await;
        assert_eq!(frames.last().unwrap()["translation_states"], json!({"en":"available", "ja":"available"}));
        let records = fixture.records();
        let pids = records.iter().map(|row| row["pid"].as_u64().unwrap()).collect::<std::collections::HashSet<_>>();
        assert_eq!(pids.len(), 1, "request rejection must not restart a healthy child");
        assert_eq!(records.iter().filter(|row| row.get("text").is_none()).count(), 1);
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn malformed_or_unknown_rejection_remains_terminal_and_cannot_publish() {
    for text in ["2010-WRONG", "2010-EXTRA", "2010-UNKNOWN", "2010-RESULT"] {
        runtime().block_on(async {
            let fixture = Fixture::new();
            let slot = worker_slot();
            let dispatcher = fixture.dispatcher(4, slot.clone());
            let session = AsrSession::default();
            let token = session.start_observed("bad-error", "ko", &["en".into()]).unwrap();
            enable(&session, &dispatcher, 2000);
            let server = ObserverServer::bind(session.clone(), 0, ORIGIN).await.unwrap();
            let mut socket = connect(&server, &token).await;
            receive(&mut socket).await;
            publish(&session, "bad", 1, "ko", text);
            let frames = until_commit(&mut socket, "rs-1-0").await;
            assert_eq!(frames.last().unwrap()["translation_states"]["en"], "failed");
            assert_eq!(frames.last().unwrap()["translations"], json!({}));
            publish(&session, "next", 1, "ko", "healthy");
            until_commit(&mut socket, "rs-2-0").await;
            let records = fixture.records();
            assert_eq!(records.iter().filter(|row| row.get("text").is_none()).count(), 2, "{text}");
            let pids = records.iter().filter(|row| row.get("text").is_some()).map(|row| row["pid"].as_u64().unwrap()).collect::<std::collections::HashSet<_>>();
            assert_eq!(pids.len(), 2, "{text}: later work requires a separately reaped replacement");
            dispatcher.stop().await.unwrap();
            assert_eq!(slot.available_permits(), 1);
            server.stop().await.unwrap();
        });
    }
}

#[test]
fn slow_native_translation_does_not_hold_asr_or_websocket_source_publication() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("slow", "ko", &["en".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "first", 1, "ko", "SLOW");
        assert_eq!(
            receive(&mut socket).await["translation_states"]["en"],
            "pending"
        );
        wait_inference(&fixture, "SLOW").await;
        let began = Instant::now();
        publish(&session, "second", 1, "ko", "NEXT");
        let next = receive(&mut socket).await;
        assert_eq!(next["original_text"], "NEXT");
        assert_eq!(next["stage"], "stable");
        assert!(began.elapsed() < Duration::from_millis(150));
        let complete = until_commit(&mut socket, next["segment_id"].as_str().unwrap()).await;
        assert_eq!(
            complete.last().unwrap()["translations"]["en"],
            "translated-en"
        );
        assert_eq!(
            fixture
                .records()
                .iter()
                .filter(|row| row.get("text").is_none())
                .count(),
            1
        );
        dispatcher.stop().await.unwrap();
        server.stop().await.unwrap();
    });
}

#[test]
fn hung_target_is_bounded_reaped_and_next_source_restarts_one_child() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("hang", "ko", &["ja".into(), "en".into()])
            .unwrap();
        enable(&session, &dispatcher, 200);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "hung", 1, "ko", "HANG");
        let first = until_commit(&mut socket, "rs-1-0").await;
        assert_eq!(
            first.last().unwrap()["translation_states"],
            json!({"ja":"available","en":"timed_out"})
        );
        assert_eq!(slot.available_permits(), 1);
        publish(&session, "retry", 1, "ko", "RECOVERED");
        let second = until_commit(&mut socket, "rs-4-0").await;
        assert_eq!(
            second.last().unwrap()["translation_states"],
            json!({"ja":"available","en":"available"})
        );
        let pids = fixture
            .records()
            .iter()
            .filter(|row| row.get("text").is_none())
            .map(|row| row["pid"].clone())
            .collect::<Vec<_>>();
        assert_eq!(pids.len(), 2);
        assert_ne!(pids[0], pids[1]);
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn saturated_queue_fails_explicitly_and_wait_consumes_the_original_deadline() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(1, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("queue", "ko", &["en".into()])
            .unwrap();
        enable(&session, &dispatcher, 500);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "active", 1, "ko", "HANG");
        receive(&mut socket).await;
        wait_inference(&fixture, "HANG").await;
        publish(&session, "queued", 1, "ko", "QUEUED-SLOW");
        publish(&session, "overflow", 1, "ko", "OVERFLOW");
        let mut completed = Vec::new();
        while completed.len() < 3 {
            let frame = receive(&mut socket).await;
            if frame["committed"] == true {
                completed.push(frame);
            }
        }
        assert!(completed
            .iter()
            .any(|frame| frame["text"] == "OVERFLOW"
                && frame["translation_states"]["en"] == "failed"));
        // A fresh 500ms budget would accept this worker's 200ms translation.
        // Its original deadline instead includes nearly 500ms spent in queue.
        assert!(completed.iter().any(|frame| frame["text"] == "QUEUED-SLOW"
            && frame["translation_states"]["en"] == "timed_out"));
        assert!(completed.iter().all(
            |frame| frame["text"] != "HANG" || frame["translation_states"]["en"] == "timed_out"
        ));
        assert!(!fixture
            .records()
            .iter()
            .any(|row| row["text"] == "OVERFLOW"));
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn precommit_revision_and_session_restart_fence_active_and_queued_results() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("old", "ko", &["en".into(), "ja".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "same", 1, "ko", "REVISION");
        let old = receive(&mut socket).await;
        wait_inference(&fixture, "REVISION").await;
        session.revise("same", 2).unwrap();
        let invalidated = receive(&mut socket).await;
        assert_eq!(invalidated["type"], "snapshot");
        assert_eq!(invalidated["segments"], json!([]));
        publish(&session, "same", 2, "ko", "NEW-REVISION");
        let revised = until_commit(&mut socket, "rs-1-0").await;
        assert!(revised.iter().all(|frame| frame["source_revision"] == 2));
        assert!(revised
            .iter()
            .all(|frame| frame["version"].as_u64().unwrap() > old["version"].as_u64().unwrap()));
        assert!(revised
            .iter()
            .all(|frame| frame["original_text"] == "NEW-REVISION"));
        publish(&session, "blocked", 1, "ko", "REVISION");
        receive(&mut socket).await;
        let new_token = session.start_observed("new", "ko", &["ja".into()]).unwrap();
        enable(&session, &dispatcher, 2000);
        let mut current = connect(&server, &new_token).await;
        assert_eq!(receive(&mut current).await["segments"], json!([]));
        publish(&session, "current", 1, "ko", "CURRENT");
        let stable = receive(&mut current).await;
        let frames = until_commit(&mut current, stable["segment_id"].as_str().unwrap()).await;
        assert!(frames
            .iter()
            .all(|frame| frame["session_id"] == "new" && frame["original_text"] == "CURRENT"));
        assert!(!fixture
            .records()
            .iter()
            .any(|row| row["text"] == "REVISION" && row["target"] == "ja"));
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn dispatcher_drop_and_stop_fail_pending_without_child_or_session_owner_cycle() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(2, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("stop", "ko", &["en".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "active", 1, "ko", "HANG");
        receive(&mut socket).await;
        wait_inference(&fixture, "HANG").await;
        publish(&session, "queued", 1, "ko", "QUEUED");
        receive(&mut socket).await;
        dispatcher.stop().await.unwrap();
        let mut failed = [receive(&mut socket).await, receive(&mut socket).await];
        failed.sort_by_key(|frame| frame["segment_id"].as_str().unwrap().to_owned());
        assert!(failed.iter().all(
            |frame| frame["committed"] == true && frame["translation_states"]["en"] == "failed"
        ));
        assert_eq!(slot.available_permits(), 1);
        session
            .start_observed("replacement", "ko", &["en".into()])
            .unwrap();
        let replacement = fixture.dispatcher(1, slot.clone());
        enable(&session, &replacement, 2000);
        publish(&session, "drop", 1, "ko", "HANG");
        wait_inference(&fixture, "HANG").await;
        drop(replacement);
        tokio::time::timeout(Duration::from_secs(2), async {
            while slot.available_permits() == 0 {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn wrong_identity_cannot_publish_and_terminal_worker_is_replaced() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(2, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("identity", "ko", &["en".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = connect(&server, &token).await;
        receive(&mut socket).await;
        publish(&session, "wrong", 1, "ko", "WRONG");
        let frames = until_commit(&mut socket, "rs-1-0").await;
        assert_eq!(frames.last().unwrap()["translation_states"]["en"], "failed");
        assert_eq!(frames.last().unwrap()["translations"], json!({}));
        publish(&session, "recovery", 1, "ko", "RECOVERED");
        let recovered = until_commit(&mut socket, "rs-3-0").await;
        assert_eq!(
            recovered.last().unwrap()["translations"]["en"],
            "translated-en"
        );
        assert_eq!(
            fixture
                .records()
                .iter()
                .filter(|row| row.get("text").is_none())
                .count(),
            2
        );
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

async fn revised_chunk_replaces_old_source_set(replacement: &[&str]) {
    let fixture = Fixture::new();
    let slot = worker_slot();
    let dispatcher = fixture.dispatcher(4, slot.clone());
    let session = AsrSession::default();
    let token = session
        .start_observed("replace", "ko", &["en".into()])
        .unwrap();
    enable(&session, &dispatcher, 2000);
    let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
        .await
        .unwrap();
    let mut socket = connect(&server, &token).await;
    receive(&mut socket).await;
    publish(&session, "unrelated", 1, "ko", "UNCHANGED");
    let unaffected = until_commit(&mut socket, "rs-1-0").await.pop().unwrap();
    publish_segments(&session, "revised", 1, "ko", &["REVISION", "REVISION"]);
    let first = receive(&mut socket).await;
    let removed = receive(&mut socket).await;
    wait_inference(&fixture, "REVISION").await;
    session.revise("revised", 2).unwrap();
    publish_segments(&session, "revised", 2, "ko", replacement);
    let invalidation = receive(&mut socket).await;
    assert_eq!(invalidation["type"], "snapshot");
    assert_eq!(invalidation["segments"], json!([unaffected]));
    assert!(invalidation["serial"].as_u64().unwrap() > removed["serial"].as_u64().unwrap());
    if !replacement.is_empty() {
        let revised = until_commit(&mut socket, first["segment_id"].as_str().unwrap()).await;
        assert!(revised.iter().all(|event| event["source_revision"] == 2));
        assert!(revised
            .iter()
            .all(|event| event["version"].as_u64().unwrap() > first["version"].as_u64().unwrap()));
    }
    let mut reconnect = connect(&server, &token).await;
    let snapshot = receive(&mut reconnect).await;
    let segments = snapshot["segments"].as_array().unwrap();
    assert_eq!(segments.len(), 1 + replacement.len());
    assert!(segments.iter().any(|event| event == &unaffected));
    assert!(!segments
        .iter()
        .any(|event| event["segment_id"] == removed["segment_id"]));
    if replacement.is_empty() {
        assert!(!segments
            .iter()
            .any(|event| event["segment_id"] == first["segment_id"]));
    }
    dispatcher.stop().await.unwrap();
    assert_eq!(slot.available_permits(), 1);
    server.stop().await.unwrap();
}

#[test]
fn revised_chunk_two_to_one_invalidates_obsolete_sources_for_live_and_reconnect() {
    runtime().block_on(revised_chunk_replaces_old_source_set(&["REPLACED"]));
}

#[test]
fn revised_chunk_two_to_empty_invalidates_obsolete_sources_for_live_and_reconnect() {
    runtime().block_on(revised_chunk_replaces_old_source_set(&[]));
}

#[test]
fn display_eviction_cannot_reset_an_uncommitted_source_version_on_revision() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let dispatcher = fixture.dispatcher(4, slot.clone());
        let session = AsrSession::default();
        let token = session
            .start_observed("eviction", "ko", &["en".into()])
            .unwrap();
        enable(&session, &dispatcher, 2000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        publish(&session, "same", 1, "ko", "REVISION");
        wait_inference(&fixture, "REVISION").await;
        // No await: the original inference completion cannot win the authority
        // race while these bounded source publications evict its display frame.
        for index in 0..40 {
            publish(
                &session,
                &format!("bulk-{index}"),
                1,
                "ko",
                &"A".repeat(20_000),
            );
        }
        assert!(session.observer_metrics().snapshot_evictions > 0);
        session.revise("same", 2).unwrap();
        publish(&session, "same", 2, "ko", "NEW");
        let mut socket = connect(&server, &token).await;
        let snapshot = receive(&mut socket).await;
        let restored = snapshot["segments"]
            .as_array()
            .unwrap()
            .iter()
            .find(|event| event["segment_id"] == "rs-1-0")
            .unwrap();
        assert_eq!(restored["source_revision"], 2);
        assert_eq!(restored["original_text"], "NEW");
        assert!(restored["version"].as_u64().unwrap() > 1);
        dispatcher.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}
