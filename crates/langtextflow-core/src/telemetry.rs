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

#[derive(Clone, Serialize)]
pub(crate) struct SourceMetrics {
    pub active_window: bool,
    pub pending_corrections: usize,
    pub pending_targets: usize,
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
                _ => {
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
