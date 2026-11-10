//! Bounded execution telemetry. No caption, model identity or filesystem values.
use crate::asr_slice::AsrSession;
use crate::caption_events::{JsonFrame, ObserverMetrics};
use crate::history::{HistoryMetrics, HistorySink};
use crate::pcm_ingress::{IngressMetrics, PcmInput};
use serde::Serialize;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use std::time::Instant;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use tokio::sync::watch;
use tokio::task::JoinHandle;

pub const METRICS_BYTES: usize = 4096;
const RING: usize = 512;
const MAX_COUNT: u64 = (1 << 53) - 1;

#[derive(Clone, Default)]
pub struct QueueTelemetry(Arc<Mutex<QueueState>>);

#[derive(Default)]
struct QueueState {
    queued: usize,
    active: usize,
    high: usize,
    admitted: u64,
    succeeded: u64,
    failed: u64,
    expired: u64,
    cancelled: u64,
    overflow: u64,
    unavailable: u64,
    waits: VecDeque<f64>,
}

#[derive(Clone, Serialize)]
pub struct QueueSnapshot {
    pub queued: usize,
    pub capacity: usize,
    pub active: usize,
    pub high: usize,
    pub admitted: u64,
    pub succeeded: u64,
    pub failed: u64,
    pub expired: u64,
    pub cancelled: u64,
    pub overflow: u64,
    pub unavailable: u64,
    pub wait_samples: usize,
    pub wait_p50_ms: Option<f64>,
    pub wait_p95_ms: Option<f64>,
}

#[derive(Clone, Copy)]
pub(crate) enum JobOutcome {
    Succeeded,
    Failed,
    Expired,
    Cancelled,
}

pub(crate) struct QueueTicket {
    queue: QueueTelemetry,
    admitted_at: Instant,
    started: bool,
    finished: bool,
}

fn increment(value: &mut u64) {
    *value = value.saturating_add(1).min(MAX_COUNT);
}

impl QueueTelemetry {
    pub(crate) fn admit(&self) -> QueueTicket {
        let mut state = self.0.lock().unwrap();
        state.queued += 1;
        state.high = state.high.max(state.queued);
        increment(&mut state.admitted);
        QueueTicket {
            queue: self.clone(),
            admitted_at: Instant::now(),
            started: false,
            finished: false,
        }
    }

    pub(crate) fn reject(&self, full: bool) {
        let mut state = self.0.lock().unwrap();
        increment(if full {
            &mut state.overflow
        } else {
            &mut state.unavailable
        });
    }

    pub fn snapshot(&self, capacity: usize) -> QueueSnapshot {
        let state = self.0.lock().unwrap();
        let mut waits = state.waits.iter().copied().collect::<Vec<_>>();
        waits.sort_by(f64::total_cmp);
        let percentile = |fraction: f64| {
            (!waits.is_empty())
                .then(|| waits[((waits.len() as f64 * fraction).ceil() as usize).saturating_sub(1)])
        };
        QueueSnapshot {
            queued: state.queued,
            capacity,
            active: state.active,
            high: state.high,
            admitted: state.admitted,
            succeeded: state.succeeded,
            failed: state.failed,
            expired: state.expired,
            cancelled: state.cancelled,
            overflow: state.overflow,
            unavailable: state.unavailable,
            wait_samples: waits.len(),
            wait_p50_ms: percentile(0.5),
            wait_p95_ms: percentile(0.95),
        }
    }
}

impl QueueTicket {
    pub(crate) fn start(&mut self) {
        if self.started || self.finished {
            return;
        }
        let mut state = self.queue.0.lock().unwrap();
        state.queued -= 1;
        state.active += 1;
        if state.waits.len() == RING {
            state.waits.pop_front();
        }
        state
            .waits
            .push_back((self.admitted_at.elapsed().as_secs_f64() * 1000.).min(86_400_000.));
        self.started = true;
    }

    pub(crate) fn finish(&mut self, outcome: JobOutcome) {
        if self.finished {
            return;
        }
        let mut state = self.queue.0.lock().unwrap();
        if self.started {
            state.active -= 1;
        } else {
            state.queued -= 1;
        }
        increment(match outcome {
            JobOutcome::Succeeded => &mut state.succeeded,
            JobOutcome::Failed => &mut state.failed,
            JobOutcome::Expired => &mut state.expired,
            JobOutcome::Cancelled => &mut state.cancelled,
        });
        self.finished = true;
    }
}

impl Drop for QueueTicket {
    fn drop(&mut self) {
        self.finish(JobOutcome::Cancelled);
    }
}

#[derive(Clone, Default, Serialize)]
pub(crate) struct SourceMetrics {
    pub active_window: bool,
    pub pending_corrections: usize,
    pub pending_targets: usize,
    pub active_provider: Option<&'static str>,
    pub auto_generation: Option<u64>,
    pub failover_count: Option<u64>,
    pub last_failover_reason: Option<crate::auto_asr::FailoverReason>,
}

#[derive(Serialize)]
pub(crate) struct MetricsFrame {
    #[serde(rename = "type")]
    kind: &'static str,
    pub session_id: String,
    pub session_generation: u64,
    pub sequence: u64,
    pub sampled_at: String,
    #[serde(rename = "final")]
    pub final_sample: bool,
    pub stop_error: Option<&'static str>,
    pub ingress: IngressMetrics,
    pub source: SourceMetrics,
    pub correction: Option<QueueSnapshot>,
    pub translation: Option<QueueSnapshot>,
    pub history: Option<HistoryMetrics>,
    pub observer: ObserverMetrics,
}

impl MetricsFrame {
    pub(crate) fn raw(&self) -> Result<JsonFrame, &'static str> {
        let raw = serde_json::value::to_raw_value(self).map_err(|_| "metrics_serialization")?;
        if raw.get().len() + 1 > METRICS_BYTES {
            return Err("metrics_limit");
        }
        Ok(Arc::from(raw))
    }
}

#[derive(Clone)]
pub(crate) struct MetricsSources {
    pub session: AsrSession,
    pub session_id: String,
    pub generation: u64,
    pub input: PcmInput,
    pub correction: Option<QueueTelemetry>,
    pub translation: Option<(QueueTelemetry, usize)>,
    pub history: Option<HistorySink>,
}

impl MetricsSources {
    fn capture(
        &self,
        sequence: u64,
        final_sample: bool,
        stop_error: Option<&'static str>,
    ) -> Result<MetricsFrame, &'static str> {
        Ok(MetricsFrame {
            kind: "metrics",
            session_id: self.session_id.clone(),
            session_generation: self.generation,
            sequence,
            sampled_at: OffsetDateTime::now_utc()
                .format(&Rfc3339)
                .map_err(|_| "clock_format")?,
            final_sample,
            stop_error,
            ingress: self.input.metrics(),
            source: self.session.source_metrics(),
            correction: self.correction.as_ref().map(|queue| queue.snapshot(4)),
            translation: self
                .translation
                .as_ref()
                .map(|(queue, capacity)| queue.snapshot(*capacity)),
            history: self.history.as_ref().map(HistorySink::metrics),
            observer: self.session.observer_metrics(),
        })
    }
}

/// Owned by control, never by AsrSession. One watch value coalesces every observer.
pub(crate) struct MetricsSampler {
    sources: MetricsSources,
    sequence: Arc<AtomicU64>,
    stop: watch::Sender<bool>,
    task: Option<JoinHandle<Result<(), &'static str>>>,
}

impl MetricsSampler {
    pub(crate) fn start(sources: MetricsSources) -> Self {
        sources.session.enable_metrics();
        let owned = sources.clone();
        let sequence = Arc::new(AtomicU64::new(0));
        let sequenced = sequence.clone();
        let (stop, mut stopping) = watch::channel(false);
        let task = tokio::spawn(async move {
            let mut timer = tokio::time::interval(Duration::from_millis(250));
            timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                if *stopping.borrow() {
                    return Ok(());
                }
                let selected = futures_util::future::select(
                    Box::pin(stopping.changed()),
                    Box::pin(timer.tick()),
                )
                .await;
                if matches!(selected, futures_util::future::Either::Left(_)) {
                    return Ok(());
                }
                let sequence = sequenced.fetch_add(1, Ordering::Relaxed) + 1;
                if sequence > MAX_COUNT {
                    return Err("metrics_sequence");
                }
                let frame = owned.capture(sequence, false, None)?.raw()?;
                if !owned
                    .session
                    .publish_metrics(&owned.session_id, owned.generation, frame)
                {
                    return Ok(());
                }
            }
        });
        Self {
            sources,
            sequence,
            stop,
            task: Some(task),
        }
    }

    pub(crate) async fn stop(&mut self) -> Result<(), &'static str> {
        let _ = self.stop.send(true);
        if let Some(mut task) = self.task.take() {
            match tokio::time::timeout(Duration::from_secs(1), &mut task).await {
                Ok(Ok(result)) => result?,
                Ok(Err(_)) => return Err("metrics_owner_failed"),
                Err(_) => {
                    task.abort();
                    let _ = task.await;
                    return Err("metrics_stop_unconfirmed");
                }
            }
        }
        Ok(())
    }

    pub(crate) fn final_frame(
        &self,
        stop_error: Option<&'static str>,
    ) -> Result<MetricsFrame, &'static str> {
        let sequence = self.sequence.fetch_add(1, Ordering::Relaxed) + 1;
        if sequence > MAX_COUNT {
            return Err("metrics_sequence");
        }
        self.sources.capture(sequence, true, stop_error)
    }
}

impl Drop for MetricsSampler {
    fn drop(&mut self) {
        let _ = self.stop.send(true);
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::asr_slice::{AsrResult, AudioChunk, InferredSegment};
    use crate::observer_ws::ObserverServer;
    use crate::{WorkerReply, WorkerStatus};
    use futures_util::StreamExt;
    use serde_json::{json, Value};
    use tokio::net::TcpStream;
    use tokio_tungstenite::{client_async, tungstenite::client::IntoClientRequest};

    fn frame(sid: &str, generation: u64, sequence: u64) -> MetricsFrame {
        MetricsFrame {
            kind: "metrics",
            session_id: sid.into(),
            session_generation: generation,
            sequence,
            sampled_at: "2026-11-10T00:00:00Z".into(),
            final_sample: false,
            stop_error: None,
            ingress: IngressMetrics {
                queue_capacity: 8,
                ..Default::default()
            },
            source: SourceMetrics {
                active_window: false,
                pending_corrections: 0,
                pending_targets: 0,
                ..Default::default()
            },
            correction: None,
            translation: None,
            history: None,
            observer: ObserverMetrics::default(),
        }
    }

    #[test]
    fn every_maximal_field_still_fits_one_four_kib_metrics_line() {
        let mut frame = frame(&"\0".repeat(128), MAX_COUNT, MAX_COUNT);
        frame.sampled_at = "9999-12-31T23:59:59.123456789+00:00".into();
        let queue = QueueSnapshot {
            queued: 16,
            capacity: 16,
            active: 1,
            high: 17,
            admitted: MAX_COUNT,
            succeeded: MAX_COUNT,
            failed: MAX_COUNT,
            expired: MAX_COUNT,
            cancelled: MAX_COUNT,
            overflow: MAX_COUNT,
            unavailable: MAX_COUNT,
            wait_samples: 512,
            wait_p50_ms: Some(1_234_567.8901234567),
            wait_p95_ms: Some(1_234_567.8901234567),
        };
        frame.correction = Some(queue.clone());
        frame.translation = Some(queue);
        frame.ingress = IngressMetrics {
            accepted_frames: MAX_COUNT,
            accepted_samples: MAX_COUNT,
            processed_samples: MAX_COUNT,
            decoded_windows: MAX_COUNT,
            empty_windows: MAX_COUNT,
            queue_depth: 8,
            queue_capacity: 8,
            queue_high_watermark: 8,
            backpressure_events: MAX_COUNT,
            clipped_samples: MAX_COUNT,
            rms_dbfs: Some(-99.12345678901235),
            voice_active: true,
            failure: Some("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        };
        frame.history = Some(HistoryMetrics {
            accepted: MAX_COUNT,
            persisted: MAX_COUNT,
            stale: MAX_COUNT,
            invalidated: MAX_COUNT,
            dropped: MAX_COUNT,
            write_errors: MAX_COUNT,
            complete: false,
            stopped: true,
            error: Some("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
            queue_depth: Some(32),
            queue_capacity: 32,
            queue_high_watermark: 32,
        });
        frame.observer = ObserverMetrics {
            capacity_rejections: MAX_COUNT,
            queue_evictions: MAX_COUNT,
            send_timeouts: MAX_COUNT,
            snapshot_evictions: MAX_COUNT,
            connected: 8,
        };
        frame.source.pending_corrections = 512;
        frame.source.pending_targets = 512 * 10;
        frame.stop_error = Some("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx");
        let size = frame.raw().unwrap().get().len() + 1;
        assert!(
            size > 1024 && size <= METRICS_BYTES,
            "bounded maximal-field line: {size}"
        );
    }

    #[test]
    fn coalesced_actual_observer_never_uses_caption_fifo_or_serial_and_reconnect_is_fenced() {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let session = AsrSession::default();
                let credential = session
                    .start_observed("owner", "en", &["ko".into()])
                    .unwrap();
                session.enable_metrics();
                let server = ObserverServer::bind(session.clone(), 0, "http://127.0.0.1")
                    .await
                    .unwrap();
                async fn connect(
                    server: &ObserverServer,
                    path: &str,
                ) -> tokio_tungstenite::WebSocketStream<TcpStream> {
                    let mut request = format!("ws://{}{}", server.address(), path)
                        .into_client_request()
                        .unwrap();
                    request
                        .headers_mut()
                        .insert("Origin", "http://127.0.0.1".parse().unwrap());
                    client_async(request, TcpStream::connect(server.address()).await.unwrap())
                        .await
                        .unwrap()
                        .0
                }
                async fn next(socket: &mut tokio_tungstenite::WebSocketStream<TcpStream>) -> Value {
                    let value = tokio::time::timeout(Duration::from_secs(3), socket.next())
                        .await
                        .unwrap()
                        .unwrap()
                        .unwrap();
                    serde_json::from_str(value.to_text().unwrap()).unwrap()
                }
                let mut socket = connect(&server, &credential.path()).await;
                assert_eq!(next(&mut socket).await["type"], "snapshot");
                // The observer deliberately does not read during a thousand samples.
                for sequence in 1..=1000 {
                    assert!(session.publish_metrics(
                        "owner",
                        1,
                        frame("owner", 1, sequence).raw().unwrap()
                    ));
                }
                assert_eq!(session.publication_serial(), 0);
                assert_eq!(session.observer_metrics().queue_evictions, 0);
                let admission = session
                    .admit(
                        "source",
                        1,
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
                    session_generation: 1,
                    segment_id: request.segment_id.clone(),
                    source_revision: 1,
                    status: WorkerStatus::Ok,
                    error: None,
                    result: Some(
                        serde_json::to_string(&AsrResult {
                            sample_count: 16000,
                            sample_rate: 16000,
                            segments: vec![InferredSegment {
                                start_seconds: 0.,
                                end_seconds: 1.,
                                text: "Owned source".into(),
                                estimated_timestamps: false,
                            }],
                        })
                        .unwrap(),
                    ),
                };
                session.finish(&admission, Some(&reply)).unwrap();
                loop {
                    let value = next(&mut socket).await;
                    if value["type"] == "transcript" && value["committed"] == true {
                        assert_eq!(value["original_text"], "Owned source");
                        break;
                    }
                }
                assert_eq!(session.publication_serial(), 2);
                let mut reconnect = connect(&server, &credential.path()).await;
                let snapshot = next(&mut reconnect).await;
                assert_eq!(snapshot["serial"], 2);
                assert_eq!(snapshot["segments"][0]["text"], "Owned source");
                assert_eq!(next(&mut reconnect).await["sequence"], 1000);
                let new = session
                    .start_observed("replacement", "en", &["ko".into()])
                    .unwrap();
                session.enable_metrics();
                assert!(!session.publish_metrics(
                    "owner",
                    1,
                    frame("owner", 1, 1001).raw().unwrap()
                ));
                assert!(session.publish_metrics(
                    "replacement",
                    2,
                    frame("replacement", 2, 1).raw().unwrap()
                ));
                let mut replacement = connect(&server, &new.path()).await;
                assert_eq!(next(&mut replacement).await["segments"], json!([]));
                let metrics = next(&mut replacement).await;
                assert_eq!(
                    (
                        metrics["session_generation"].as_u64(),
                        metrics["sequence"].as_u64()
                    ),
                    (Some(2), Some(1))
                );
                server.stop().await.unwrap();
                session.stop();
            });
    }
    #[test]
    fn queue_population_is_exact_once_and_wait_ring_is_bounded() {
        let queue = QueueTelemetry::default();
        assert!(queue.snapshot(4).wait_p95_ms.is_none());
        let mut ticket = queue.admit();
        assert_eq!(queue.snapshot(4).queued, 1);
        ticket.start();
        ticket.start();
        ticket.finish(JobOutcome::Succeeded);
        ticket.finish(JobOutcome::Failed);
        drop(ticket);
        for _ in 0..600 {
            let mut ticket = queue.admit();
            ticket.start();
        }
        queue.reject(true);
        queue.reject(false);
        let snapshot = queue.snapshot(4);
        assert_eq!((snapshot.queued, snapshot.active, snapshot.high), (0, 0, 1));
        assert_eq!(
            (snapshot.admitted, snapshot.succeeded, snapshot.cancelled),
            (601, 1, 600)
        );
        assert_eq!((snapshot.overflow, snapshot.unavailable), (1, 1));
        assert_eq!(snapshot.wait_samples, RING);
        assert!(snapshot.wait_p95_ms.unwrap().is_finite());
    }
}
