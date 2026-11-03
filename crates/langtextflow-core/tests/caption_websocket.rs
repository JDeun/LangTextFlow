use futures_util::{SinkExt, StreamExt};
use langtextflow_core::asr_slice::{Admission, AsrResult, AsrSession, AudioChunk, InferredSegment};
use langtextflow_core::caption_events::{ObserverCredential, MAX_OBSERVERS, MAX_SNAPSHOT_BYTES};
use langtextflow_core::observer_ws::ObserverServer;
use langtextflow_core::{WorkerReply, WorkerStatus};
use serde_json::Value;
use std::time::{Duration, Instant};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_tungstenite::{
    client_async,
    tungstenite::{client::IntoClientRequest, Message},
    WebSocketStream,
};

const ORIGIN: &str = "http://127.0.0.1:5173";
type Client = WebSocketStream<TcpStream>;

fn runtime() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap()
}

fn reply(admitted: &Admission, text: &str) -> WorkerReply {
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
                    text: text.into(),
                    estimated_timestamps: false,
                }],
            })
            .unwrap(),
        ),
    }
}

fn admit(session: &AsrSession, id: &str, revision: u64, start: u64) -> Admission {
    session
        .admit(
            id,
            revision,
            "ko",
            AudioChunk {
                wav_path: "not-read-by-synthetic-fixture.wav".into(),
                start_sample: start,
                end_sample: start + 16_000,
                sample_rate: 16_000,
            },
            2000,
        )
        .unwrap()
}

fn publish(session: &AsrSession, id: &str, start: u64, text: &str) {
    let admitted = admit(session, id, 1, start);
    session
        .finish(&admitted, Some(&reply(&admitted, text)))
        .unwrap();
}

async fn connect(server: &ObserverServer, path: &str, origin: &str) -> Result<Client, ()> {
    let mut request = format!("ws://{}{}", server.address(), path)
        .into_client_request()
        .unwrap();
    request
        .headers_mut()
        .insert("Origin", origin.parse().unwrap());
    let stream = TcpStream::connect(server.address()).await.map_err(|_| ())?;
    client_async(request, stream)
        .await
        .map(|(client, _)| client)
        .map_err(|_| ())
}

async fn client(server: &ObserverServer, token: &ObserverCredential) -> Client {
    connect(server, &token.path(), ORIGIN).await.unwrap()
}

async fn receive(client: &mut Client) -> Value {
    let message = tokio::time::timeout(Duration::from_secs(2), client.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    serde_json::from_str(message.to_text().unwrap()).unwrap()
}

#[test]
fn actual_websocket_snapshot_first_full_schema_and_source_only_commit() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let credential = session
            .start_observed("session-a", "ko", &["en".into(), "ja".into()])
            .unwrap();
        publish(&session, &"X".repeat(128), 0, "Original ASR source");
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = client(&server, &credential).await;
        let snapshot = receive(&mut socket).await;
        assert_eq!(snapshot["type"], "snapshot");
        assert_eq!(snapshot["segments"].as_array().unwrap().len(), 1);
        let first = &snapshot["segments"][0];
        assert_eq!(first["stage"], "committed");
        assert_eq!(first["version"], 2);
        assert_eq!(first["original_text"], first["text"]);
        let id = first["segment_id"].as_str().unwrap();
        assert!(
            id.len() <= 128
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.:-".contains(&b))
        );
        publish(&session, "next", 16_000, "Second source");
        let stable = receive(&mut socket).await;
        let committed = receive(&mut socket).await;
        assert_eq!(stable["stage"], "stable");
        assert_eq!(stable["committed"], false);
        assert_eq!(committed["stage"], "committed");
        assert_eq!(committed["committed"], true);
        assert_eq!(stable["version"], 1);
        assert_eq!(committed["version"], 2);
        assert_eq!(stable["original_text"], committed["original_text"]);
        assert_eq!(committed["translations"], serde_json::json!({}));
        assert_eq!(
            committed["translation_states"],
            serde_json::json!({"en":"unsupported","ja":"unsupported"})
        );
        assert_eq!(
            committed["asr_provenance"]["audio_chunk_start_sample"],
            16_000
        );
        assert_eq!(
            committed["asr_provenance"]["audio_chunk_end_sample"],
            32_000
        );
        assert!(committed["asr_provenance"]["verified_model"].is_null());
        assert!(committed["asr_provenance"]["configured_model"].is_null());
        assert_eq!(
            committed["asr_provenance"]["timestamp_basis"],
            "sample_clock_native_hint"
        );
        assert!(committed["emitted_at"].as_str().unwrap().ends_with('Z'));
        assert!(committed["speaker"].is_null() && committed["correction"].is_null());
        assert!(!committed
            .to_string()
            .contains(credential.path().split('=').nth(1).unwrap()));
        socket.close(None).await.unwrap();
        let mut reconnect = client(&server, &credential).await;
        let restored = receive(&mut reconnect).await;
        assert_eq!(restored["segments"].as_array().unwrap().len(), 2);
        assert_eq!(restored["segments"][1]["original_text"], "Second source");
        assert_eq!(restored["segments"][1]["version"], 2);
        server.stop().await.unwrap();
    });
}

#[test]
fn wrong_origin_token_and_old_session_cannot_observe_new_source() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let old = session.start_observed("old", "ko", &["en".into()]).unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        assert!(connect(&server, &old.path(), "https://untrusted.example")
            .await
            .is_err());
        assert!(connect(
            &server,
            &format!("/ws/observer?token={}", "0".repeat(64)),
            ORIGIN
        )
        .await
        .is_err());
        let mut old_client = client(&server, &old).await;
        receive(&mut old_client).await;
        let delayed = admit(&session, "pending", 1, 0);
        let new = session.start_observed("new", "ko", &["ja".into()]).unwrap();
        assert!(session
            .finish(&delayed, Some(&reply(&delayed, "LEAKED OLD SOURCE")))
            .is_err());
        assert!(connect(&server, &old.path(), ORIGIN).await.is_err());
        let close = tokio::time::timeout(Duration::from_secs(1), old_client.next())
            .await
            .unwrap();
        assert!(matches!(
            close,
            Some(Ok(Message::Close(_))) | None | Some(Err(_))
        ));
        let mut current = client(&server, &new).await;
        let snapshot = receive(&mut current).await;
        assert_eq!(snapshot["session_id"], "new");
        assert_eq!(snapshot["segments"], serde_json::json!([]));
        publish(&session, "new-source", 0, "New original");
        assert_eq!(receive(&mut current).await["text"], "New original");
        assert_eq!(receive(&mut current).await["session_id"], "new");
        session.stop();
        assert!(connect(&server, &new.path(), ORIGIN).await.is_err());
        server.stop().await.unwrap();
    });
}

#[test]
fn stale_revision_never_publishes_and_new_revision_keeps_source_identity() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("revision", "ko", &["en".into()])
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut socket = client(&server, &token).await;
        receive(&mut socket).await;
        let stale = admit(&session, "same-source", 1, 0);
        session.revise("same-source", 2).unwrap();
        assert!(session.finish(&stale, Some(&reply(&stale, "OLD"))).is_err());
        let new = admit(&session, "same-source", 2, 0);
        session.finish(&new, Some(&reply(&new, "NEW"))).unwrap();
        let stable = receive(&mut socket).await;
        let committed = receive(&mut socket).await;
        assert_eq!(stable["text"], "NEW");
        assert_eq!(stable["source_revision"], 2);
        assert_eq!(stable["segment_id"], "rs-1-0");
        assert_eq!(stable["segment_id"], committed["segment_id"]);
        publish(&session, "different-source", 16_000, "NEW");
        let other = receive(&mut socket).await;
        receive(&mut socket).await;
        assert_ne!(other["segment_id"], stable["segment_id"]);
        server.stop().await.unwrap();
    });
}

#[test]
fn slow_observer_is_evicted_without_blocking_fast_source_publication_or_reconnect() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("load", "ko", &["en".into()])
            .unwrap();
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let _slow = client(&server, &token).await;
        let mut fast = client(&server, &token).await;
        receive(&mut fast).await;
        let text = "A".repeat(20_000);
        let mut publish_time = Duration::ZERO;
        for index in 0..64 {
            let began = Instant::now();
            publish(&session, &format!("chunk-{index}"), index * 16_000, &text);
            publish_time += began.elapsed();
            receive(&mut fast).await;
            let event = receive(&mut fast).await;
            assert_eq!(event["stage"], "committed");
        }
        assert!(publish_time < Duration::from_secs(2));
        tokio::time::sleep(Duration::from_millis(300)).await;
        let metrics = session.observer_metrics();
        assert!(metrics.queue_evictions + metrics.send_timeouts > 0);
        let mut restored = client(&server, &token).await;
        let snapshot = receive(&mut restored).await;
        assert!(snapshot.to_string().len() <= MAX_SNAPSHOT_BYTES);
        assert!(snapshot["segments"].as_array().unwrap().len() <= 100);
        assert!(snapshot["snapshot_evictions"].as_u64().unwrap() > 0);
        assert!(snapshot["segments"]
            .as_array()
            .unwrap()
            .iter()
            .all(|event| event["stage"] == "committed"));
        server.stop().await.unwrap();
    });
}

#[test]
fn bounded_unauthenticated_handshakes_read_only_clients_and_shutdown() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("bounds", "ko", &["en".into()])
            .unwrap();
        assert!(
            ObserverServer::bind(session.clone(), 0, "https://remote.example")
                .await
                .is_err()
        );
        let server = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut client = client(&server, &token).await;
        receive(&mut client).await;
        client
            .send(Message::Text(
                "{\"type\":\"transcript\",\"text\":\"forged\"}".into(),
            ))
            .await
            .unwrap();
        assert!(matches!(
            tokio::time::timeout(Duration::from_secs(1), client.next()).await,
            Ok(Some(Err(_))) | Ok(None) | Ok(Some(Ok(Message::Close(_))))
        ));
        let mut held = Vec::new();
        for _ in 0..MAX_OBSERVERS {
            let mut stream = TcpStream::connect(server.address()).await.unwrap();
            stream
                .write_all(b"GET /ws/observer HTTP/1.1\r\n")
                .await
                .unwrap();
            held.push(stream);
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
        let mut extra = TcpStream::connect(server.address()).await.unwrap();
        let mut byte = [0_u8; 1];
        assert!(matches!(
            tokio::time::timeout(Duration::from_secs(1), extra.read(&mut byte)).await,
            Ok(Ok(0)) | Ok(Err(_))
        ));
        assert!(session.observer_metrics().capacity_rejections > 0);
        let started = Instant::now();
        server.stop().await.unwrap();
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(session.observer_metrics().connected, 0);
    });
}

#[test]
fn stopping_old_listener_cannot_disconnect_replacement_listener_clients() {
    runtime().block_on(async {
        let session = AsrSession::default();
        let token = session
            .start_observed("same-session", "ko", &["en".into()])
            .unwrap();
        let old = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let replacement = ObserverServer::bind(session.clone(), 0, ORIGIN)
            .await
            .unwrap();
        let mut old_client = client(&old, &token).await;
        receive(&mut old_client).await;
        let mut current = client(&replacement, &token).await;
        receive(&mut current).await;
        old.stop().await.unwrap();
        assert_eq!(session.observer_metrics().connected, 1);
        publish(&session, "source", 0, "Still healthy");
        assert_eq!(receive(&mut current).await["text"], "Still healthy");
        assert_eq!(receive(&mut current).await["stage"], "committed");
        replacement.stop().await.unwrap();
        assert_eq!(session.observer_metrics().connected, 0);
    });
}
