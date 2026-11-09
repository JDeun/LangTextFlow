//! Source-first correction arbitration; the Python child only evaluates leaves.
use crate::asr_slice::CorrectionJob;
use crate::source_context::SourceContext;
use crate::supervision::{SupervisedWorker, WorkerError};
use crate::{validate_reply, WorkerRequest, WorkerStatus};
use futures_util::future::{select, Either};
use serde::{Deserialize, Serialize};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, watch, Semaphore};
use tokio::task::JoinHandle;

#[derive(Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CorrectionOptions {
    pub provider: String,
    pub url: String,
    pub model: String,
    pub timeout_seconds: f64,
    #[serde(default, skip_serializing)]
    pub public_model: Option<String>,
}

impl CorrectionOptions {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        if !matches!(self.provider.as_str(), "none" | "ollama")
            || self.url.len() > 4096
            || self.model.is_empty()
            || self.model.chars().count() > 200
            || !self.timeout_seconds.is_finite()
            || !(0.1..=120.).contains(&self.timeout_seconds)
            || self.public_model.as_ref().is_some_and(|model| {
                model
                    .as_bytes()
                    .first()
                    .is_none_or(|byte| !byte.is_ascii_alphanumeric())
                    || model.len() > 200
                    || model.starts_with('.')
                    || model.contains("..")
                    || model.contains(":/")
                    || model.split('/').count() > 2
                    || model.split('/').any(str::is_empty)
                    || (model.as_bytes().get(1) == Some(&b':')
                        && model.as_bytes()[0].is_ascii_alphabetic())
                    || ["sk-", "hf_", "ghp_", "github_pat_", "xoxb-", "xoxp-"]
                        .iter()
                        .any(|prefix| model.starts_with(*prefix))
                    || std::path::Path::new(model).is_dir()
                    || !model
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"_.:-/".contains(&byte))
            })
        {
            return Err("invalid_correction_options");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_model_identity_cannot_publish_locations_endpoints_or_token_prefixes() {
        let mut options = CorrectionOptions {
            provider: "ollama".into(),
            url: "http://127.0.0.1".into(),
            model: "configured:tag".into(),
            timeout_seconds: 1.,
            public_model: Some("configured:tag".into()),
        };
        assert!(options.validate().is_ok());
        for value in [
            "/private-missing",
            "C:private",
            "https://host/model",
            "hf_synthetic_token",
            "../secret",
        ] {
            options.public_model = Some(value.into());
            assert!(options.validate().is_err());
        }
    }
}

#[derive(Clone, Serialize)]
pub struct CorrectionProvenance {
    pub method: &'static str,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub deterministic_changed: bool,
    pub llm_attempted: bool,
    pub llm_applied: bool,
    pub changed: bool,
    pub fallback_reason: Option<&'static str>,
}

pub(crate) struct CorrectionDecision {
    pub text: String,
    pub deterministic_text: String,
    pub provenance: CorrectionProvenance,
}

impl CorrectionDecision {
    pub(crate) fn fallback(
        source: &str,
        deterministic: &str,
        options: &CorrectionOptions,
        attempted: bool,
        reason: &'static str,
    ) -> Self {
        Self {
            text: deterministic.into(),
            deterministic_text: deterministic.into(),
            provenance: CorrectionProvenance {
                method: "fallback",
                provider: (options.provider != "none").then(|| options.provider.clone()),
                model: (options.provider != "none")
                    .then(|| options.public_model.clone())
                    .flatten(),
                deterministic_changed: deterministic != source,
                llm_attempted: attempted,
                llm_applied: false,
                changed: deterministic != source,
                fallback_reason: Some(reason),
            },
        }
    }
}

#[derive(Clone)]
pub(crate) struct CorrectionAdmission {
    pub sender: mpsc::Sender<CorrectionJob>,
    pub options: CorrectionOptions,
}

pub(crate) fn leaf_request(
    base: &WorkerRequest,
    mode: &str,
    text: &str,
    context: &SourceContext,
    options: &CorrectionOptions,
) -> WorkerRequest {
    let mut request = base.clone();
    request.request_id = format!("{}:{mode}", base.request_id);
    request.payload =
        serde_json::json!({"mode":mode,"text":text,"context":context,"options":options})
            .to_string();
    request
}

type Reaped = Arc<Mutex<Option<Arc<AtomicBool>>>>;

pub struct CorrectionDispatcher {
    pub(crate) sender: mpsc::Sender<CorrectionJob>,
    pub available: bool,
    pub(crate) options: CorrectionOptions,
    stop: watch::Sender<bool>,
    task: Option<JoinHandle<Result<(), &'static str>>>,
    reaped: Reaped,
}

impl CorrectionDispatcher {
    pub async fn start(
        program: String,
        args: Vec<String>,
        options: CorrectionOptions,
        prepare: WorkerRequest,
        slot: Arc<Semaphore>,
    ) -> Result<Self, &'static str> {
        options.validate()?;
        let reaped: Reaped = Arc::new(Mutex::new(None));
        let mut worker = None;
        let mut available = false;
        if options.provider == "ollama" {
            if let Ok(spawned) = spawn(&program, &args, &slot, &reaped) {
                let result =
                    prepare_worker(spawned, &prepare, Instant::now() + Duration::from_secs(3))
                        .await;
                worker = result.0;
                available = result.1;
            }
        }
        let (sender, receiver) = mpsc::channel(4);
        let (stop, stopping) = watch::channel(false);
        let task = tokio::spawn(run(
            program,
            args,
            options.clone(),
            prepare,
            slot,
            reaped.clone(),
            worker,
            available,
            receiver,
            stopping,
        ));
        Ok(Self {
            sender,
            available,
            options,
            stop,
            task: Some(task),
            reaped,
        })
    }

    pub async fn stop(mut self) -> Result<(), &'static str> {
        let _ = self.stop.send(true);
        let mut task = self.task.take().ok_or("correction_owner_missing")?;
        let outcome = match tokio::time::timeout(Duration::from_secs(2), &mut task).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("correction_owner_failed"),
            Err(_) => {
                task.abort();
                let _ = task.await;
                Err("correction_stop_timeout")
            }
        };
        let signal = self.reaped.lock().unwrap().clone();
        let deadline = Instant::now() + Duration::from_secs(2);
        while signal
            .as_ref()
            .is_some_and(|signal| !signal.load(Ordering::Acquire))
            && Instant::now() < deadline
        {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        if signal.is_some_and(|signal| !signal.load(Ordering::Acquire)) {
            return Err("correction_reap_unconfirmed");
        }
        outcome
    }
}

impl Drop for CorrectionDispatcher {
    fn drop(&mut self) {
        let _ = self.stop.send(true);
        if let Some(task) = self.task.take() {
            task.abort();
        }
    }
}

fn spawn(
    program: &str,
    args: &[String],
    slot: &Arc<Semaphore>,
    reaped: &Reaped,
) -> Result<SupervisedWorker, WorkerError> {
    let worker = SupervisedWorker::spawn(
        program,
        &args.iter().map(String::as_str).collect::<Vec<_>>(),
        slot.clone(),
    )?;
    *reaped.lock().unwrap() = Some(worker.reaped_signal());
    Ok(worker)
}

async fn prepare_worker(
    worker: SupervisedWorker,
    request: &WorkerRequest,
    deadline: Instant,
) -> (Option<SupervisedWorker>, bool) {
    match worker
        .exchange(request, &AtomicBool::new(false), deadline)
        .await
    {
        Ok((worker, reply))
            if validate_reply(
                request,
                &reply,
                request.session_generation,
                request.source_revision,
            )
            .is_ok()
                && (reply.result.as_deref() == Some("ready")
                    || reply.error.as_deref() == Some("correction_provider_unavailable")) =>
        {
            let available =
                reply.status == WorkerStatus::Ok && reply.result.as_deref() == Some("ready");
            (Some(worker), available)
        }
        Ok((mut worker, _)) => {
            let _ = worker.stop().await;
            (None, false)
        }
        Err(_) => (None, false),
    }
}

async fn evaluate(
    worker: SupervisedWorker,
    request: &WorkerRequest,
    job: &CorrectionJob,
) -> (Option<SupervisedWorker>, Result<String, &'static str>) {
    match worker.exchange(request, &job.cancelled, job.deadline).await {
        Ok((worker, reply))
            if validate_reply(
                request,
                &reply,
                request.session_generation,
                request.source_revision,
            )
            .is_ok()
                && reply.status == WorkerStatus::Ok
                && reply.result.as_ref().is_some_and(|text| {
                    !text.trim().is_empty() && text.chars().count() <= 20_000
                }) =>
        {
            if Instant::now() >= job.deadline {
                (
                    Some(worker),
                    Err("correction timed out; using deterministic result"),
                )
            } else {
                (Some(worker), Ok(reply.result.unwrap()))
            }
        }
        Ok((worker, reply))
            if validate_reply(
                request,
                &reply,
                request.session_generation,
                request.source_revision,
            )
            .is_ok()
                && reply.status == WorkerStatus::Error
                && reply.error.as_deref() == Some("correction_rejected") =>
        {
            (
                Some(worker),
                Err("correction provider failed; using deterministic result"),
            )
        }
        Ok((mut worker, _)) => {
            let _ = worker.stop().await;
            (
                None,
                Err("correction provider failed; using deterministic result"),
            )
        }
        Err(WorkerError::Deadline) => (
            None,
            Err("correction timed out; using deterministic result"),
        ),
        Err(_) => (
            None,
            Err("correction provider failed; using deterministic result"),
        ),
    }
}

#[allow(clippy::too_many_arguments)]
async fn run(
    program: String,
    args: Vec<String>,
    options: CorrectionOptions,
    prepare: WorkerRequest,
    slot: Arc<Semaphore>,
    reaped: Reaped,
    mut worker: Option<SupervisedWorker>,
    mut available: bool,
    mut receiver: mpsc::Receiver<CorrectionJob>,
    mut stopping: watch::Receiver<bool>,
) -> Result<(), &'static str> {
    loop {
        if *stopping.borrow() {
            break;
        }
        let Some(mut job) =
            (match select(Box::pin(stopping.changed()), Box::pin(receiver.recv())).await {
                Either::Left(_) => break,
                Either::Right((job, _)) => job,
            })
        else {
            break;
        };
        if job.cancelled.load(Ordering::Acquire) || Instant::now() >= job.deadline {
            continue;
        }
        if worker.is_none() {
            worker = spawn(&program, &args, &slot, &reaped).ok();
            if options.provider == "ollama" && available && worker.is_some() {
                let preparing = prepare_worker(worker.take().unwrap(), &prepare, job.deadline);
                let result = match select(Box::pin(stopping.changed()), Box::pin(preparing)).await {
                    Either::Left(_) => break,
                    Either::Right((result, _)) => result,
                };
                worker = result.0;
                available = result.1;
            }
        }
        let Some(owned) = worker.take() else { continue };
        let deterministic_request = leaf_request(
            &job.request,
            "deterministic",
            &job.source,
            &job.context,
            &options,
        );
        let response = match select(
            Box::pin(stopping.changed()),
            Box::pin(evaluate(owned, &deterministic_request, &job)),
        )
        .await
        {
            Either::Left(_) => break,
            Either::Right((response, _)) => response,
        };
        worker = response.0;
        let Ok(deterministic) = response.1 else {
            continue;
        };
        let decision = if options.provider == "none" {
            CorrectionDecision {
                text: deterministic.clone(),
                deterministic_text: deterministic.clone(),
                provenance: CorrectionProvenance {
                    method: "deterministic",
                    provider: None,
                    model: None,
                    deterministic_changed: deterministic != job.source,
                    llm_attempted: false,
                    llm_applied: false,
                    changed: deterministic != job.source,
                    fallback_reason: None,
                },
            }
        } else if !available || worker.is_none() {
            CorrectionDecision::fallback(
                &job.source,
                &deterministic,
                &options,
                false,
                "correction provider unavailable",
            )
        } else {
            let llm_request =
                leaf_request(&job.request, "llm", &deterministic, &job.context, &options);
            let response = match select(
                Box::pin(stopping.changed()),
                Box::pin(evaluate(worker.take().unwrap(), &llm_request, &job)),
            )
            .await
            {
                Either::Left(_) => break,
                Either::Right((response, _)) => response,
            };
            worker = response.0;
            match response.1 {
                Ok(text) => CorrectionDecision {
                    provenance: CorrectionProvenance {
                        method: "llm",
                        provider: Some(options.provider.clone()),
                        model: options.public_model.clone(),
                        deterministic_changed: deterministic != job.source,
                        llm_attempted: true,
                        llm_applied: true,
                        changed: text != job.source,
                        fallback_reason: None,
                    },
                    text,
                    deterministic_text: deterministic.clone(),
                },
                Err(reason) => CorrectionDecision::fallback(
                    &job.source,
                    &deterministic,
                    &options,
                    true,
                    reason,
                ),
            }
        };
        job.finish(decision);
    }
    receiver.close();
    drop(receiver);
    if let Some(mut worker) = worker {
        worker.stop().await.map_err(|_| "correction_reap_failed")?;
    }
    Ok(())
}
