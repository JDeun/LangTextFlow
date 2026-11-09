//! Opt-in bounded native MT lane. Rust owns admission and publication, Python inference only.
use crate::asr_slice::MtJob;
use crate::supervision::{SupervisedWorker, WorkerError};
use crate::translation_provider::{TranslationIdentity, TranslationProvider};
use crate::{validate_reply, WorkerRequest, WorkerStatus};
use crate::telemetry::{JobOutcome, QueueTelemetry};
use futures_util::future::{select, Either};
use serde::Serialize;
use serde_json::Value;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, watch, Semaphore};
use tokio::task::JoinHandle;

const MAX_CONTEXT_BYTES: usize = 64 * 1024;
pub(crate) struct ContextBytes(pub(crate) usize);

impl std::io::Write for ContextBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > MAX_CONTEXT_BYTES - self.0 {
            return Err(std::io::Error::other("context_limit"));
        }
        self.0 += bytes.len();
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[derive(Clone, Serialize)]
pub struct TranslationContext {
    pub preset: String,
    pub glossary: Vec<Value>,
}

impl TranslationContext {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if !matches!(
            self.preset.as_str(),
            "general" | "church" | "conference" | "lecture"
        ) || self.glossary.len() > 500
            || self.glossary.iter().any(|entry| !entry.is_object())
        {
            return Err("invalid_context");
        }
        serde_json::to_writer(&mut ContextBytes(0), self).map_err(|_| "invalid_context")?;
        // The shipped worker validates the existing SessionContext/GlossaryEntry
        // contract before loading native state; Rust does not duplicate that schema.
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn context_counter_accepts_near_limit_and_rejects_without_output_allocation() {
        let near = TranslationContext {
            preset: "general".into(),
            glossary: vec![serde_json::json!({"term":"x".repeat(118)}); 500],
        };
        let mut counted = ContextBytes(0);
        serde_json::to_writer(&mut counted, &near).unwrap();
        assert!((MAX_CONTEXT_BYTES - 1024..=MAX_CONTEXT_BYTES).contains(&counted.0));
        assert!(near.validate().is_ok());
        let oversized = TranslationContext {
            preset: "general".into(),
            glossary: vec![serde_json::json!({"term":"한".repeat(30_000)})],
        };
        let mut counted = ContextBytes(0);
        assert!(serde_json::to_writer(&mut counted, &oversized).is_err());
        assert!(counted.0 <= MAX_CONTEXT_BYTES);
        assert!(oversized.validate().is_err());
    }
}

#[derive(Clone)]
pub(crate) struct MtAdmission {
    pub sender: mpsc::Sender<MtJob>,
    pub context: TranslationContext,
    pub deadline_ms: u64,
    pub metrics: QueueTelemetry,
}

pub(crate) enum MtOutcome {
    Available(String),
    Failed,
    TimedOut,
}

/// Caller owns this task and reuses the SAME native slot for replacement lanes.
/// Jobs retain only weak caption authority; there is no session/task ownership cycle.
pub struct MtDispatcher {
    pub(crate) sender: mpsc::Sender<MtJob>,
    pub(crate) metrics: QueueTelemetry,
    pub(crate) queue_capacity: usize,
    stop: watch::Sender<bool>,
    task: Option<JoinHandle<Result<(), &'static str>>>,
    slot: Arc<Semaphore>,
    pub(crate) identity: TranslationIdentity,
    health: Arc<AtomicBool>,
}

impl MtDispatcher {
    pub fn start(
        program: String,
        args: Vec<String>,
        queue_capacity: usize,
        slot: Arc<Semaphore>,
    ) -> Result<Self, &'static str> {
        if !(1..=16).contains(&queue_capacity) {
            return Err("invalid_translation_capacity");
        }
        tokio::runtime::Handle::try_current().map_err(|_| "runtime_required")?;
        let (sender, receiver) = mpsc::channel(queue_capacity);
        let (stop, stopping) = watch::channel(false);
        let health = Arc::new(AtomicBool::new(true));
        let task = tokio::spawn(run(
            program,
            args,
            slot.clone(),
            receiver,
            stopping,
            None,
            None,
            true,
            health.clone(),
        ));
        Ok(Self {
            sender,
            metrics: QueueTelemetry::default(),
            queue_capacity,
            stop,
            task: Some(task),
            slot,
            identity: TranslationIdentity::native(),
            health,
        })
    }

    pub fn available(&self) -> bool {
        self.health.load(Ordering::Acquire)
    }

    pub async fn start_prepared(
        program: String,
        args: Vec<String>,
        slot: Arc<Semaphore>,
        provider: TranslationProvider,
        prepare: WorkerRequest,
        mut parent_closed: watch::Receiver<bool>,
    ) -> Result<Self, &'static str> {
        let provider = Arc::new(provider);
        let identity = provider.options.identity();
        let (sender, receiver) = mpsc::channel(4);
        let (stop, stopping) = watch::channel(false);
        let mut worker = None;
        let mut available = false;
        let request = provider.request(&prepare, "prepare", "");
        let deadline = Instant::now() + Duration::from_millis(request.deadline_ms);
        if !*parent_closed.borrow() {
            if let Ok(spawned) = SupervisedWorker::spawn(
                &program,
                &args.iter().map(String::as_str).collect::<Vec<_>>(),
                slot.clone(),
            ) {
                let cancelled = AtomicBool::new(false);
                let exchange = spawned.exchange(&request, &cancelled, deadline);
                match select(Box::pin(parent_closed.changed()), Box::pin(exchange)).await {
                    Either::Left(_) => {}
                    Either::Right((Ok((mut returned, reply)), _)) => {
                        if validate_reply(
                            &request,
                            &reply,
                            request.session_generation,
                            request.source_revision,
                        )
                        .is_ok()
                            && reply.status == WorkerStatus::Ok
                            && reply.result.as_deref() == Some("ready")
                        {
                            available = true;
                            worker = Some(returned);
                        } else {
                            let _ = returned.stop().await;
                        }
                    }
                    Either::Right(_) => {}
                };
            }
        }
        // Prepare cancellation/failure must release the same owned native lease.
        if worker.is_none() {
            let until = Instant::now() + Duration::from_secs(4);
            while slot.available_permits() == 0 && Instant::now() < until {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
            if slot.available_permits() == 0 {
                return Err("translation_reap_unconfirmed");
            }
        }
        let health = Arc::new(AtomicBool::new(available));
        let task = tokio::spawn(run(
            program,
            args,
            slot.clone(),
            receiver,
            stopping,
            Some(provider),
            worker,
            available,
            health.clone(),
        ));
        Ok(Self {
            sender,
            metrics: QueueTelemetry::default(),
            queue_capacity: 4,
            stop,
            task: Some(task),
            slot,
            identity,
            health,
        })
    }

    pub async fn stop(mut self) -> Result<(), &'static str> {
        let _ = self.stop.send(true);
        let mut task = self.task.take().ok_or("translation_owner_missing")?;
        let outcome = match tokio::time::timeout(Duration::from_secs(2), &mut task).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("translation_owner_failed"),
            Err(_) => {
                task.abort();
                let _ = task.await;
                Err("translation_stop_timeout")
            }
        };
        // An aborted owned exchange uses Supervisor's independent reaper. Do
        // not report clean shutdown merely because its async owner returned.
        let deadline = Instant::now() + Duration::from_secs(2);
        while self.slot.available_permits() == 0 && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        if self.slot.available_permits() == 0 {
            return Err("translation_reap_unconfirmed");
        }
        outcome
    }
}

impl Drop for MtDispatcher {
    fn drop(&mut self) {
        let _ = self.stop.send(true);
        if let Some(task) = self.task.take() {
            task.abort();
        }
        // Worker Drop kills/reaps independently and retains the shared slot
        // until confirmed exit, including after this Tokio runtime disappears.
    }
}

#[allow(clippy::too_many_arguments)]
async fn run(
    program: String,
    args: Vec<String>,
    slot: Arc<Semaphore>,
    mut receiver: mpsc::Receiver<MtJob>,
    mut stopping: watch::Receiver<bool>,
    provider: Option<Arc<TranslationProvider>>,
    mut worker: Option<SupervisedWorker>,
    prepared: bool,
    health: Arc<AtomicBool>,
) -> Result<(), &'static str> {
    loop {
        if *stopping.borrow() {
            break;
        }
        let next = match select(Box::pin(stopping.changed()), Box::pin(receiver.recv())).await {
            Either::Left(_) => break,
            Either::Right((job, _)) => job,
        };
        let Some(mut job) = next else { break };
        job.start_metrics();
        if !prepared {
            job.finish_metrics(JobOutcome::Failed);
            job.finish(MtOutcome::Failed);
            continue;
        }
        if job.cancelled() {
            job.finish_metrics(JobOutcome::Cancelled);
            job.finish(MtOutcome::Failed);
            continue;
        }
        if Instant::now() >= job.deadline {
            job.finish_metrics(JobOutcome::Expired);
            job.finish(MtOutcome::TimedOut);
            continue;
        }
        if worker.is_none() {
            let arguments = args.iter().map(String::as_str).collect::<Vec<_>>();
            match SupervisedWorker::spawn(&program, &arguments, slot.clone()) {
                Ok(spawned) => {
                    if let Some(provider) = &provider {
                        let mut request = provider.request(&job.request, "prepare", "");
                        request.request_id.push_str(":prepare");
                        let cancelled = job.cancelled.clone();
                        let exchange = spawned.exchange(&request, &cancelled, job.deadline);
                        match select(Box::pin(stopping.changed()), Box::pin(exchange)).await {
                            Either::Left(_) => break,
                            Either::Right((Ok((mut returned, reply)), _)) => {
                                if validate_reply(
                                    &request,
                                    &reply,
                                    request.session_generation,
                                    request.source_revision,
                                )
                                .is_ok()
                                    && reply.status == WorkerStatus::Ok
                                    && reply.result.as_deref() == Some("ready")
                                {
                                    worker = Some(returned);
                                } else {
                                    let _ = returned.stop().await;
                                    health.store(false, Ordering::Release);
                                    job.finish_metrics(JobOutcome::Failed);
                                    job.finish(MtOutcome::Failed);
                                    continue;
                                }
                            }
                            Either::Right((Err(_), _)) => {
                                health.store(false, Ordering::Release);
                                job.finish_metrics(JobOutcome::Failed);
                                job.finish(MtOutcome::Failed);
                                continue;
                            }
                        };
                    } else {
                        worker = Some(spawned);
                    }
                }
                Err(_) => {
                    health.store(false, Ordering::Release);
                    job.finish_metrics(JobOutcome::Failed);
                    job.finish(MtOutcome::Failed);
                    continue;
                }
            }
        }
        if let Some(provider) = &provider {
            job.request = provider.request(&job.request, "translate", &job.source);
        }
        let exchange = worker
            .take()
            .unwrap()
            .exchange(&job.request, &job.cancelled, job.deadline);
        let response = match select(Box::pin(stopping.changed()), Box::pin(exchange)).await {
            Either::Left(_) => break,
            Either::Right((result, _)) => result,
        };
        match response {
            Ok((mut returned, reply)) => {
                let valid = validate_reply(
                    &job.request,
                    &reply,
                    job.request.session_generation,
                    job.request.source_revision,
                )
                .is_ok();
                let candidate = reply.result.as_deref().filter(|text| {
                    !text.trim().is_empty()
                        && text.chars().count() <= 20_000
                        && (provider.as_ref().is_some_and(|value| !value.native())
                            || ((text.trim().to_lowercase() != job.source.trim().to_lowercase()
                                || text.trim().bytes().all(|byte| byte.is_ascii_digit()))
                                && !text.trim_start().starts_with(['{', '['])))
                });
                if let Some(result) =
                    candidate.filter(|_| valid && reply.status == WorkerStatus::Ok)
                {
                    let result = result.to_owned();
                    health.store(true, Ordering::Release);
                    worker = Some(returned);
                    job.finish_metrics(JobOutcome::Succeeded);
                    job.finish(MtOutcome::Available(result));
                } else if valid
                    && reply.status == WorkerStatus::Error
                    && reply.error.as_deref() == Some("translation_rejected")
                {
                    // Completed-output rejection fails only this target, not the healthy model.
                    worker = Some(returned);
                    health.store(true, Ordering::Release);
                    job.finish_metrics(JobOutcome::Failed);
                    job.finish(MtOutcome::Failed);
                } else {
                    // Native/unknown errors and invalid identity or framing remain terminal.
                    let _ = returned.stop().await;
                    health.store(false, Ordering::Release);
                    job.finish_metrics(JobOutcome::Failed);
                    job.finish(MtOutcome::Failed);
                }
            }
            Err(WorkerError::Deadline) => {
                health.store(false, Ordering::Release);
                job.finish_metrics(JobOutcome::Expired);
                job.finish(MtOutcome::TimedOut);
            }
            Err(_) => {
                health.store(false, Ordering::Release);
                job.finish_metrics(JobOutcome::Failed);
                job.finish(MtOutcome::Failed);
            }
        }
    }
    receiver.close();
    drop(receiver); // Armed queued-job drops publish explicit terminal failure.
    if let Some(mut worker) = worker {
        worker
            .stop()
            .await
            .map_err(|_| "translation_reap_unconfirmed")?;
    }
    Ok(())
}
