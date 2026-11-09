//! Bounded execution telemetry. No caption, model identity or filesystem values.
use serde::Serialize;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Instant;

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
