//! Actual child/observer lifecycle; fixture inference is synthetic, never model evidence.
use futures_util::StreamExt;
use langtextflow_core::asr_slice::{AsrResult, AsrSession, AudioChunk, InferredSegment};
use langtextflow_core::correction_slice::{CorrectionDispatcher, CorrectionOptions};
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::source_context::SourceContext;
use langtextflow_core::supervision::worker_slot;
use langtextflow_core::{TaskKind, WorkerReply, WorkerRequest, WorkerStatus};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio::sync::Semaphore;
use tokio_tungstenite::{client_async, tungstenite::client::IntoClientRequest, WebSocketStream};

const ORIGIN: &str = "http://127.0.0.1:5185";
const CHILD: &str = r#"
import json,os,sys,time
log=sys.argv[1]
for line in sys.stdin:
    request=json.loads(line); payload=json.loads(request['payload'])
    text=payload.get('text',''); mode=payload.get('mode','mt')
    with open(log,'a') as out: out.write(json.dumps({'pid':os.getpid(),'text':text,'mode':mode})+'\n')
    reply={key:request[key] for key in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
    error=None
    if mode=='prepare': result='ready'
    elif mode=='deterministic':
        if 'LATE' in text: time.sleep(30)
        if 'SLOW' in text: time.sleep(.15)
        result=text.replace('RAW','DET')
        if 'WRONG' in text: reply['request_id']='wrong'
    elif mode=='llm':
        if 'HANG' in text: time.sleep(30)
        if 'REJECT' in text: error='correction_rejected'; result=None
        else: result=text.replace('DET','LLM')
    else:
        if 'SLOW' in text: time.sleep(.1)
        result='translated-'+text
        if request['target_language']=='ja': error='translation_rejected'; result=None
    reply.update(status='error' if error else 'ok',result=result,error=error)
    print(json.dumps(reply),flush=True)
"#;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "ltf-correction-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn python(&self) -> String {
        std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python3".into())
    }
    fn args(&self) -> Vec<String> {
        vec![
            "-u".into(),
            "-c".into(),
            CHILD.into(),
            self.0.join("calls.jsonl").to_string_lossy().into(),
        ]
    }
    fn records(&self) -> Vec<Value> {
        std::fs::read_to_string(self.0.join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }
    async fn correction(
        &self,
        session: &AsrSession,
        provider: &str,
        seconds: f64,
        slot: Arc<Semaphore>,
    ) -> CorrectionDispatcher {
        let options = CorrectionOptions {
            provider: provider.into(),
            url: "http://127.0.0.1".into(),
            model: "fixture:tag".into(),
            timeout_seconds: seconds,
            public_model: Some("fixture:tag".into()),
        };
        let prepare = WorkerRequest {
            protocol_version: 1,
            request_id: "prepare".into(),
            session_id: "session".into(),
            session_generation: 1,
            segment_id: "prepare".into(),
            source_revision: 1,
            task_kind: TaskKind::Correction,
            source_language: "en".into(),
            target_language: None,
            deadline_ms: 3000,
            payload: json!({"mode":"prepare","text":"","context":{},"options":options}).to_string(),
        };
        let dispatcher =
            CorrectionDispatcher::start(self.python(), self.args(), options.clone(), prepare, slot)
                .await
                .unwrap();
        session.enable_correction(&dispatcher, options).unwrap();
        dispatcher
    }
    fn mt(&self, session: &AsrSession, milliseconds: u64) -> MtDispatcher {
        let dispatcher = MtDispatcher::start(self.python(), self.args(), 4, worker_slot()).unwrap();
        session
            .enable_translation(
                &dispatcher,
                TranslationContext {
                    preset: "general".into(),
                    glossary: vec![],
                },
                milliseconds,
            )
            .unwrap();
        dispatcher
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(self.0.join("calls.jsonl"));
        let _ = std::fs::remove_dir(&self.0);
    }
}
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn session() -> (
    AsrSession,
    langtextflow_core::caption_events::ObserverCredential,
) {
    let session = AsrSession::default();
    let credential = session
        .start_observed("session", "en", &["ko".into(), "ja".into()])
        .unwrap();
    session
        .configure_context("en", SourceContext::default())
        .unwrap();
    (session, credential)
}
fn publish(session: &AsrSession, id: &str, revision: u64, text: &str) {
    let admission = session
        .admit(
            id,
            revision,
            "en",
            AudioChunk {
                wav_path: "synthetic.wav".into(),
                start_sample: 0,
                end_sample: 16000,
                sample_rate: 16000,
            },
            1000,
        )
        .unwrap();
    let request = admission.request();
    let reply = WorkerReply {
        protocol_version: 1,
        request_id: request.request_id.clone(),
        session_id: request.session_id.clone(),
        session_generation: request.session_generation,
        segment_id: request.segment_id.clone(),
        source_revision: revision,
        status: WorkerStatus::Ok,
        error: None,
        result: Some(
            serde_json::to_string(&AsrResult {
                sample_count: 16000,
                sample_rate: 16000,
                segments: vec![InferredSegment {
                    start_seconds: 0.,
                    end_seconds: 1.,
                    text: text.into(),
                    estimated_timestamps: false,
                }],
            })
            .unwrap(),
        ),
    };
    session.finish(&admission, Some(&reply)).unwrap();
}
async fn connect(
    server: &ObserverServer,
    credential: &langtextflow_core::caption_events::ObserverCredential,
) -> WebSocketStream<TcpStream> {
    let mut request = format!("ws://{}{}", server.address(), credential.path())
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
async fn next(client: &mut WebSocketStream<TcpStream>) -> Value {
    serde_json::from_str(
        tokio::time::timeout(Duration::from_secs(3), client.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap()
            .to_text()
            .unwrap(),
    )
    .unwrap()
}
async fn terminal(client: &mut WebSocketStream<TcpStream>) -> Vec<Value> {
    let mut frames = vec![];
    loop {
        let frame = next(client).await;
        let committed = frame["committed"] == true;
        frames.push(frame);
        if committed {
            return frames;
        }
    }
}

#[test]
fn raw_source_precedes_correction_and_mt_receives_selected_text_with_immutable_original() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "ollama", 1., worker_slot())
            .await;
        let mt = fixture.mt(&session, 1000);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        assert_eq!(next(&mut client).await["type"], "snapshot");
        publish(&session, "chunk", 1, "RAW");
        let frames = terminal(&mut client).await;
        assert_eq!(frames[0]["stage"], "stable");
        assert_eq!(frames[0]["text"], "RAW");
        let corrected = frames
            .iter()
            .find(|frame| frame["stage"] == "corrected")
            .unwrap();
        assert_eq!(corrected["text"], "LLM");
        assert_eq!(corrected["correction"]["method"], "llm");
        assert_eq!(corrected["correction"]["deterministic_changed"], true);
        assert!(frames.iter().all(|frame| frame["original_text"] == "RAW"));
        let final_frame = frames.last().unwrap();
        assert_eq!(
            final_frame["translation_states"],
            json!({"ko":"available","ja":"failed"})
        );
        assert_eq!(final_frame["translations"]["ko"], "translated-LLM");
        assert!(fixture
            .records()
            .iter()
            .filter(|record| record["mode"] == "mt")
            .all(|record| record["text"] == "LLM"));
        let mut reconnect = connect(&server, &credential).await;
        assert_eq!(
            next(&mut reconnect).await["segments"][0]["correction"]["method"],
            "llm"
        );
        correction.stop().await.unwrap();
        mt.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn correction_wait_consumes_the_original_target_deadline_instead_of_renewing_mt() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "none", 1., worker_slot())
            .await;
        let mt = fixture.mt(&session, 200);
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        publish(&session, "chunk", 1, "RAW-SLOW");
        let frames = terminal(&mut client).await;
        assert_eq!(frames[0]["text"], "RAW-SLOW");
        assert_eq!(
            frames.last().unwrap()["translation_states"],
            json!({"ko":"timed_out","ja":"timed_out"})
        );
        assert!(frames.last().unwrap()["translations"]
            .as_object()
            .unwrap()
            .is_empty());
        correction.stop().await.unwrap();
        mt.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn hung_llm_falls_back_to_completed_deterministic_without_holding_raw_source() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "ollama", 0.15, worker_slot())
            .await;
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        publish(&session, "chunk", 1, "RAW-HANG");
        let raw = next(&mut client).await;
        assert_eq!(raw["stage"], "stable");
        assert_eq!(raw["text"], "RAW-HANG");
        assert_eq!(raw["committed"], false);
        assert_eq!(session.pending_translation_targets(), 0);
        assert_eq!(session.pending_postprocessing(), 1);
        let frames = terminal(&mut client).await;
        let final_frame = frames.last().unwrap();
        assert_eq!(final_frame["text"], "DET-HANG");
        assert_eq!(final_frame["original_text"], "RAW-HANG");
        assert_eq!(final_frame["correction"]["method"], "fallback");
        assert_eq!(final_frame["correction"]["llm_attempted"], true);
        assert_eq!(final_frame["correction"]["llm_applied"], false);
        assert_eq!(
            final_frame["translation_states"],
            json!({"ko":"unsupported","ja":"unsupported"})
        );
        assert_eq!(session.pending_postprocessing(), 0);
        correction.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn revision_cancels_old_correction_and_only_new_source_reaches_reconnect() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "none", 1., worker_slot())
            .await;
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        publish(&session, "chunk", 1, "RAW-LATE");
        assert_eq!(next(&mut client).await["text"], "RAW-LATE");
        let deadline = Instant::now() + Duration::from_secs(2);
        while fixture.records().is_empty() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        session.revise("chunk", 2).unwrap();
        publish(&session, "chunk", 2, "RAW-new");
        assert_eq!(next(&mut client).await["type"], "snapshot");
        let frames = terminal(&mut client).await;
        assert!(frames.iter().all(|frame| frame["source_revision"] == 2));
        assert_eq!(frames.last().unwrap()["text"], "DET-new");
        let mut reconnect = connect(&server, &credential).await;
        assert_eq!(
            next(&mut reconnect).await["segments"][0]["original_text"],
            "RAW-new"
        );
        correction.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn stopped_owner_confirms_its_own_reap_even_when_a_replacement_holds_shared_capacity() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, _) = session();
        let slot = worker_slot();
        let correction = fixture.correction(&session, "none", 1., slot.clone()).await;
        publish(&session, "chunk", 1, "RAW-LATE");
        let deadline = Instant::now() + Duration::from_secs(2);
        while fixture.records().is_empty() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        assert!(!fixture.records().is_empty());
        let replacement = tokio::spawn(slot.clone().acquire_owned());
        correction.stop().await.unwrap();
        let replacement = tokio::time::timeout(Duration::from_secs(1), replacement)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert_eq!(slot.available_permits(), 0);
        assert_eq!(session.pending_postprocessing(), 0);
        drop(replacement);
        session.stop();
    });
}

#[test]
fn stopped_correction_owner_retains_known_phase_without_claiming_a_queued_llm_attempt() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "ollama", 2., worker_slot())
            .await;
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        publish(&session, "first", 1, "RAW-HANG");
        let deadline = Instant::now() + Duration::from_secs(1);
        while !fixture.records().iter().any(|entry| entry["mode"] == "llm") {
            assert!(Instant::now() < deadline);
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        publish(&session, "queued", 1, "RAW-QUEUED");
        correction.stop().await.unwrap();
        let mut reconnect = connect(&server, &credential).await;
        let snapshot = next(&mut reconnect).await;
        let frames = snapshot["segments"].as_array().unwrap();
        assert_eq!(frames.len(), 2);
        for (text, chosen, attempted) in [
            ("RAW-HANG", "DET-HANG", true),
            ("RAW-QUEUED", "RAW-QUEUED", false),
        ] {
            let frame = frames
                .iter()
                .find(|frame| frame["original_text"] == text)
                .unwrap();
            assert_eq!(frame["text"], chosen);
            assert_eq!(frame["committed"], true);
            assert_eq!(frame["correction"]["method"], "fallback");
            assert_eq!(frame["correction"]["llm_attempted"], attempted);
            assert_eq!(frame["correction"]["llm_applied"], false);
            assert_eq!(frame["correction"]["deterministic_changed"], attempted);
        }
        assert_eq!(
            fixture
                .records()
                .iter()
                .filter(|entry| entry["mode"] == "llm")
                .count(),
            1
        );
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn bounded_queue_overflow_preserves_every_raw_source_and_terminates_pending_work() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "none", 0.2, worker_slot())
            .await;
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        let reader = tokio::spawn(async move {
            let mut frames = vec![];
            let mut committed = 0;
            while committed < 8 {
                let frame = next(&mut client).await;
                if frame["committed"] == true {
                    committed += 1;
                }
                frames.push(frame);
            }
            frames
        });
        for index in 0..8 {
            publish(
                &session,
                &format!("chunk-{index}"),
                1,
                if index == 0 { "RAW-LATE" } else { "RAW-next" },
            );
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        let frames = reader.await.unwrap();
        assert_eq!(
            frames
                .iter()
                .filter(|frame| frame["stage"] == "stable")
                .count(),
            8
        );
        assert_eq!(
            frames
                .iter()
                .filter(|frame| frame["committed"] == true)
                .count(),
            8
        );
        assert!(frames
            .iter()
            .any(|frame| frame["correction"]["method"] == "fallback"));
        assert_eq!(session.pending_postprocessing(), 0);
        // Eight windows cannot expand into eight waiting OS children.
        let pids = fixture
            .records()
            .iter()
            .map(|value| value["pid"].as_u64().unwrap())
            .collect::<std::collections::HashSet<_>>();
        assert!(pids.len() <= 5); // One active plus four admitted; overflow never creates workers.
        correction.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}

#[test]
fn wrong_identity_fails_closed_then_a_new_child_can_correct_the_next_source() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let (session, credential) = session();
        let correction = fixture
            .correction(&session, "none", 1., worker_slot())
            .await;
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = connect(&server, &credential).await;
        next(&mut client).await;
        publish(&session, "bad", 1, "RAW-WRONG");
        let failed = terminal(&mut client).await;
        assert_eq!(failed.last().unwrap()["text"], "RAW-WRONG");
        assert_eq!(failed.last().unwrap()["correction"]["method"], "fallback");
        publish(&session, "next", 1, "RAW-good");
        let next_frames = terminal(&mut client).await;
        assert_eq!(next_frames.last().unwrap()["text"], "DET-good");
        let records = fixture.records();
        assert_ne!(records[0]["pid"], records[1]["pid"]);
        correction.stop().await.unwrap();
        server.stop().await.unwrap();
        session.stop();
    });
}
