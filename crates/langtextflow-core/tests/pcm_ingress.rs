//! Continuous-input ownership evidence. Actual children/WS, synthetic inference only.
use futures_util::StreamExt;
use langtextflow_core::asr_slice::AsrSession;
use langtextflow_core::caption_events::ObserverCredential;
use langtextflow_core::mt_slice::{MtDispatcher, TranslationContext};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::pcm_ingress::{PcmIngress, QUEUE_FRAMES, WINDOW_SAMPLES};
use langtextflow_core::supervision::worker_slot;
use serde_json::{json, Value};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio_tungstenite::{client_async, tungstenite::client::IntoClientRequest, WebSocketStream};

const ORIGIN: &str = "http://127.0.0.1:5185";
const WORKER: &str = r#"
import json, os, struct, sys, time, wave
log, mode = sys.argv[1:]
def record(value):
    with open(log,'a') as out: out.write(json.dumps({'pid':os.getpid(),**value})+'\n')
record({'stage':'spawn'})
for line in sys.stdin:
    request = json.loads(line)
    payload = json.loads(request['payload'])
    if request['task_kind']=='translation':
        target=request['target_language']
        reply={key:request[key] for key in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
        failed=target=='ja'
        reply.update(status='error' if failed else 'ok',error='fixture-failed' if failed else None,result=None if failed else 'translated-'+target)
        print(json.dumps(reply),flush=True)
        if failed: sys.exit(1)
        continue
    with wave.open(payload['wav_path'],'rb') as audio:
        assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth())==(16000,1,2)
        assert payload['start_sample']==0 and payload['end_sample']==audio.getnframes()
        raw=audio.readframes(audio.getnframes())
        samples=struct.unpack('<'+'h'*(len(raw)//2),raw)
        record({'stage':'infer','count':len(samples),'first':samples[0],'last':samples[-1],
                'all_zero':not any(samples),'file_start':payload['start_sample'],
                'file_end':payload['end_sample'],'file':payload['wav_path']})
        if mode=='hang': time.sleep(60) # Keep the actual WAV read handle open.
        if mode=='crash': os._exit(7)
    segments=[] if not any(samples) else [{'start_seconds':0,'end_seconds':len(samples)/16000,
                                         'text':'synthetic source','estimated_timestamps':mode=='estimated'}]
    reply={key:request[key] for key in ('protocol_version','request_id','session_id','session_generation','segment_id','source_revision')}
    reply.update(status='ok',error=None,result=json.dumps({'sample_count':len(samples),'sample_rate':16000,'segments':segments}))
    print(json.dumps(reply),flush=True)
"#;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "ltf-ingress-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn python(&self) -> String {
        std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python3".into())
    }
    fn arguments(&self, mode: &str) -> Vec<String> {
        vec![
            "-u".into(),
            "-c".into(),
            WORKER.into(),
            self.0.join("calls.jsonl").to_string_lossy().into(),
            mode.into(),
        ]
    }
    fn records(&self) -> Vec<Value> {
        std::fs::read_to_string(self.0.join("calls.jsonl"))
            .unwrap_or_default()
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    }
    fn roots(&self) -> Vec<PathBuf> {
        std::fs::read_dir(&self.0)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| path.is_dir())
            .collect()
    }
    fn start(
        &self,
        session: AsrSession,
        mode: &str,
        slot: std::sync::Arc<tokio::sync::Semaphore>,
    ) -> PcmIngress {
        let arguments = self.arguments(mode);
        PcmIngress::start(
            session,
            "ko",
            &self.python(),
            &arguments.iter().map(String::as_str).collect::<Vec<_>>(),
            slot,
            &self.0,
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(self.0.join("calls.jsonl"));
        let _ = std::fs::remove_dir(&self.0);
    }
}

type Client = WebSocketStream<TcpStream>;
fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}
fn pcm(value: f32, samples: usize) -> Vec<u8> {
    value.to_le_bytes().repeat(samples)
}
async fn client(server: &ObserverServer, credential: &ObserverCredential) -> Client {
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
async fn receive(client: &mut Client) -> Value {
    let frame = tokio::time::timeout(Duration::from_secs(3), client.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    serde_json::from_str(frame.to_text().unwrap()).unwrap()
}
async fn wait_inference(fixture: &Fixture) {
    tokio::time::timeout(Duration::from_secs(3), async {
        while !fixture.records().iter().any(|row| row["stage"] == "infer") {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}
async fn wait_cleanup(fixture: &Fixture, slot: &tokio::sync::Semaphore) {
    tokio::time::timeout(Duration::from_secs(3), async {
        while slot.available_permits() == 0 || !fixture.roots().is_empty() {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}

#[test]
fn varied_continuous_frames_preserve_silence_pcm_clock_final_tail_and_ws_provenance() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        let token = session
            .start_observed("continuous", "ko", &["en".into()])
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = client(&server, &token).await;
        receive(&mut socket).await;
        let owner = fixture.start(session.clone(), "normal", slot.clone());
        let input = owner.input();
        let mut stream = pcm(0.5, WINDOW_SAMPLES);
        stream.extend(pcm(0.0, WINDOW_SAMPLES));
        stream.extend(pcm(-0.25, 160));
        let mut position = 0;
        let lengths = [17, 4097, 16384, 3, 32001];
        let mut index = 0;
        while position < stream.len() {
            let count = (lengths[index % lengths.len()] * 4).min(stream.len() - position);
            input
                .feed(&stream[position..position + count])
                .await
                .unwrap();
            position += count;
            index += 1;
            if index == 2 {
                let before = input.metrics().accepted_samples;
                tokio::time::sleep(Duration::from_millis(30)).await;
                assert_eq!(input.metrics().accepted_samples, before);
            }
        }
        let report = owner.finish().await.unwrap();
        assert_eq!(report.accepted_samples, 128160);
        assert_eq!(report.processed_samples, 128160);
        assert_eq!(report.decoded_windows, 3);
        assert_eq!(report.empty_windows, 1);
        assert!(report.failure.is_none());
        assert!(report.queue_high_watermark <= QUEUE_FRAMES);
        let mut committed = Vec::new();
        while committed.len() < 2 {
            let frame = receive(&mut socket).await;
            if frame["committed"] == true {
                committed.push(frame);
            }
        }
        assert_eq!(
            (
                committed[0]["start_ms"].as_u64().unwrap(),
                committed[0]["end_ms"].as_u64().unwrap()
            ),
            (0, 4000)
        );
        assert_eq!(
            (
                committed[1]["start_ms"].as_u64().unwrap(),
                committed[1]["end_ms"].as_u64().unwrap()
            ),
            (8000, 8010)
        );
        assert_eq!(
            committed[1]["asr_provenance"]["audio_chunk_start_sample"],
            128000
        );
        assert_eq!(
            committed[1]["asr_provenance"]["audio_chunk_end_sample"],
            128160
        );
        assert_eq!(
            committed[1]["asr_provenance"]["timestamp_basis"],
            "sample_clock_native_hint"
        );
        assert!(committed
            .iter()
            .all(|event| event["original_text"] == event["text"]));
        let records = fixture
            .records()
            .into_iter()
            .filter(|row| row["stage"] == "infer")
            .collect::<Vec<_>>();
        assert_eq!(
            records
                .iter()
                .map(|row| row["count"].as_u64().unwrap())
                .collect::<Vec<_>>(),
            vec![64000, 64000, 160]
        );
        assert_eq!(records[0]["first"], 16384);
        assert_eq!(records[1]["all_zero"], true);
        assert_eq!(records[2]["first"], -8192);
        assert!(records.iter().all(|row| row["file_start"] == 0));
        assert!(fixture.roots().is_empty());
        assert_eq!(slot.available_permits(), 1);
        let mut reconnect = client(&server, &token).await;
        assert_eq!(
            receive(&mut reconnect).await["segments"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        server.stop().await.unwrap();
    });
}

#[test]
fn energy_hangover_and_rejected_pcm_never_drop_or_advance_the_source_clock() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        session.start("vad").unwrap();
        let owner = fixture.start(session, "normal", slot.clone());
        let input = owner.input();
        input.feed(&pcm(0.5, 1)).await.unwrap();
        assert!(input.metrics().voice_active);
        for invalid in [
            vec![],
            vec![0],
            f32::NAN.to_le_bytes().to_vec(),
            f32::INFINITY.to_le_bytes().to_vec(),
            pcm(4.01, 1),
            vec![0; 1024 * 1024 + 4],
        ] {
            assert!(input.feed(&invalid).await.is_err());
            assert_eq!(input.metrics().accepted_samples, 1);
        }
        for _ in 0..3 {
            input.feed(&pcm(0.0, 1)).await.unwrap();
            assert!(input.metrics().voice_active);
        }
        input.feed(&pcm(0.0, 1)).await.unwrap();
        assert!(!input.metrics().voice_active);
        input.feed(&pcm(4.0, 1)).await.unwrap();
        assert_eq!(input.metrics().clipped_samples, 1);
        let metrics = owner.finish().await.unwrap();
        assert_eq!(metrics.accepted_samples, 6);
        assert_eq!(metrics.processed_samples, 6);
        assert_eq!(metrics.accepted_frames, 6);
        assert_eq!(
            fixture
                .records()
                .iter()
                .find(|row| row["stage"] == "infer")
                .unwrap()["count"],
            6
        );
        assert!(fixture.roots().is_empty());
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn full_queue_backpressures_one_producer_then_cancel_unblocks_without_admitting_tail() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        session.start("queue").unwrap();
        let owner = fixture.start(session.clone(), "hang", slot.clone());
        let input = owner.input();
        let frame = pcm(0.5, WINDOW_SAMPLES);
        input.feed(&frame).await.unwrap();
        wait_inference(&fixture).await;
        for _ in 0..QUEUE_FRAMES {
            input.feed(&frame).await.unwrap();
        }
        let accepted = input.metrics().accepted_samples;
        let pending_input = input.clone();
        let pending_frame = frame.clone();
        let pending = tokio::spawn(async move { pending_input.feed(&pending_frame).await });
        tokio::time::sleep(Duration::from_millis(20)).await;
        assert!(!pending.is_finished());
        assert_eq!(input.metrics().accepted_samples, accepted);
        assert_eq!(input.feed(&frame).await.unwrap_err(), "audio_producer_busy");
        let report = owner.cancel().await.unwrap();
        assert!(pending.await.unwrap().is_err());
        assert_eq!(report.accepted_samples, accepted);
        assert_eq!(report.processed_samples, 0);
        assert_eq!(report.queue_high_watermark, QUEUE_FRAMES);
        assert_eq!(report.failure, Some("audio_cancelled"));
        assert!(fixture.roots().is_empty());
        assert_eq!(slot.available_permits(), 1);
        session.start("retry").unwrap();
        let retry = fixture.start(session, "normal", slot.clone());
        retry.input().feed(&frame).await.unwrap();
        assert_eq!(
            retry.finish().await.unwrap().processed_samples,
            WINDOW_SAMPLES as u64
        );
        assert_eq!(slot.available_permits(), 1);
        assert!(fixture.roots().is_empty());
    });
}

#[test]
fn session_restart_cancels_old_native_work_and_cannot_relabel_old_pcm_as_new_source() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        let old_token = session.start_observed("old", "ko", &["en".into()]).unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let owner = fixture.start(session.clone(), "hang", slot.clone());
        let input = owner.input();
        input.feed(&pcm(0.5, WINDOW_SAMPLES)).await.unwrap();
        wait_inference(&fixture).await;
        input.feed(&pcm(0.5, WINDOW_SAMPLES)).await.unwrap();
        let token = session.start_observed("new", "ko", &["en".into()]).unwrap();
        assert!(input.feed(&pcm(0.5, 1)).await.is_err());
        wait_cleanup(&fixture, &slot).await;
        assert!(owner.finish().await.is_err());
        let mut socket = client(&server, &token).await;
        assert_eq!(receive(&mut socket).await["segments"], json!([]));
        let retry = fixture.start(session.clone(), "estimated", slot.clone());
        retry.input().feed(&pcm(0.5, 160)).await.unwrap();
        retry.finish().await.unwrap();
        let stable = receive(&mut socket).await;
        let committed = receive(&mut socket).await;
        assert_eq!(stable["session_id"], "new");
        assert_eq!(committed["start_ms"], 0);
        assert_eq!(committed["end_ms"], 10);
        assert_eq!(
            committed["asr_provenance"]["timestamp_basis"],
            "sample_clock_estimated"
        );
        assert_ne!(old_token.path(), token.path());
        assert!(fixture.roots().is_empty());
        server.stop().await.unwrap();
    });
}

#[test]
fn idle_old_ingress_exits_on_session_restart_even_if_producer_clones_survive() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        session.start("old").unwrap();
        let owner = fixture.start(session.clone(), "normal", slot.clone());
        let input = owner.input();
        session.start("new").unwrap();
        wait_cleanup(&fixture, &slot).await;
        assert!(input.feed(&pcm(0.5, 1)).await.is_err());
        assert!(owner.finish().await.is_err());
        let retry = fixture.start(session, "normal", slot.clone());
        retry.input().feed(&pcm(0.0, 1)).await.unwrap();
        assert_eq!(retry.finish().await.unwrap().empty_windows, 1);
        assert!(fixture.roots().is_empty());
    });
}

#[test]
fn active_source_revision_cancels_continuous_window_without_publishing_stale_text() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        let token = session
            .start_observed("revision", "ko", &["en".into()])
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let owner = fixture.start(session.clone(), "hang", slot.clone());
        let input = owner.input();
        input.feed(&pcm(0.5, WINDOW_SAMPLES)).await.unwrap();
        wait_inference(&fixture).await;
        session.revise("pcm-1", 2).unwrap();
        wait_cleanup(&fixture, &slot).await;
        assert!(owner.finish().await.is_err());
        assert_eq!(input.metrics().processed_samples, 0);
        let mut socket = client(&server, &token).await;
        assert_eq!(receive(&mut socket).await["segments"], json!([]));
        assert_eq!(slot.available_permits(), 1);
        server.stop().await.unwrap();
    });
}

#[test]
fn external_finish_timeout_consumes_owner_reaps_child_and_removes_its_open_window() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        session.start("cancel-finish").unwrap();
        let owner = fixture.start(session, "hang", slot.clone());
        let input = owner.input();
        input.feed(&pcm(0.5, WINDOW_SAMPLES)).await.unwrap();
        wait_inference(&fixture).await;
        assert!(
            tokio::time::timeout(Duration::from_millis(20), owner.finish())
                .await
                .is_err()
        );
        wait_cleanup(&fixture, &slot).await;
        assert_eq!(input.metrics().failure, Some("audio_owner_dropped"));
        assert!(input.feed(&pcm(0.5, 1)).await.is_err());
        assert_eq!(slot.available_permits(), 1);
    });
}

#[test]
fn drain_timeout_is_failed_and_still_confirms_child_and_spool_cleanup() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        session.start("drain").unwrap();
        let owner = fixture.start(session, "hang", slot.clone());
        let input = owner.input();
        input.feed(&pcm(0.5, WINDOW_SAMPLES)).await.unwrap();
        wait_inference(&fixture).await;
        let started = Instant::now();
        assert_eq!(owner.finish().await.unwrap_err(), "audio_drain_timeout");
        assert!(started.elapsed() < Duration::from_secs(13));
        assert_eq!(input.metrics().failure, Some("audio_drain_timeout"));
        assert_eq!(slot.available_permits(), 1);
        assert!(fixture.roots().is_empty());
    });
}

#[test]
fn continuous_asr_source_reaches_existing_mt_and_canonical_websocket_commit() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        let token = session
            .start_observed("asr-mt", "ko", &["en".into(), "ja".into()])
            .unwrap();
        let arguments = fixture.arguments("normal");
        let mt_slot = worker_slot();
        let mt = MtDispatcher::start(fixture.python(), arguments, 4, mt_slot.clone()).unwrap();
        session
            .enable_translation(
                &mt,
                TranslationContext {
                    preset: "general".into(),
                    glossary: vec![],
                },
                2000,
            )
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = client(&server, &token).await;
        receive(&mut socket).await;
        let owner = fixture.start(session, "normal", slot.clone());
        owner.input().feed(&pcm(0.5, 160)).await.unwrap();
        owner.finish().await.unwrap();
        let first = receive(&mut socket).await;
        let partial = receive(&mut socket).await;
        let committed = receive(&mut socket).await;
        assert_eq!(first["stage"], "stable");
        assert_eq!(
            first["translation_states"],
            json!({"en":"pending","ja":"pending"})
        );
        assert_eq!(partial["version"], 2);
        assert_eq!(committed["version"], 3);
        assert_eq!(committed["stage"], "committed");
        assert_eq!(
            committed["translation_states"],
            json!({"en":"available","ja":"failed"})
        );
        assert_eq!(committed["translations"], json!({"en":"translated-en"}));
        assert_eq!(committed["original_text"], first["original_text"]);
        mt.stop().await.unwrap();
        assert_eq!(slot.available_permits(), 1);
        assert_eq!(mt_slot.available_permits(), 1);
        assert!(fixture.roots().is_empty());
        server.stop().await.unwrap();
    });
}

#[test]
fn ninety_minutes_plus_one_window_fast_replay_preserves_bounded_history_and_old_terminal_fence() {
    runtime().block_on(async {
        let fixture = Fixture::new();
        let slot = worker_slot();
        let session = AsrSession::default();
        let token = session
            .start_observed("long-synthetic", "ko", &["en".into()])
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let owner = fixture.start(session.clone(), "normal", slot.clone());
        let input = owner.input();
        let frame = pcm(0.5, WINDOW_SAMPLES);
        for index in 0..1351 {
            input.feed(&frame).await.unwrap();
            if index == 136 {
                tokio::time::timeout(Duration::from_secs(3), async {
                    while input.metrics().decoded_windows < 129 {
                        tokio::time::sleep(Duration::from_millis(5)).await;
                    }
                })
                .await
                .unwrap();
                assert!(session.revise("pcm-1", 2).is_err());
            }
        }
        let metrics = owner.finish().await.unwrap();
        assert_eq!(metrics.decoded_windows, 1351);
        assert_eq!(metrics.processed_samples, 1351 * WINDOW_SAMPLES as u64);
        assert_eq!(metrics.accepted_samples, metrics.processed_samples);
        assert!(session.revise("pcm-1", 2).is_err());
        let mut socket = client(&server, &token).await;
        let snapshot = receive(&mut socket).await;
        let segments = snapshot["segments"].as_array().unwrap();
        assert_eq!(segments.len(), 100);
        assert_eq!(segments.last().unwrap()["end_ms"], 1351 * 4000);
        assert!(segments.iter().all(|event| event["committed"] == true));
        assert_eq!(
            fixture
                .records()
                .iter()
                .filter(|row| row["stage"] == "infer")
                .count(),
            1351
        );
        assert_eq!(slot.available_permits(), 1);
        assert!(fixture.roots().is_empty());
        server.stop().await.unwrap();
    });
}
