//! Opt-in model-worker supervision. The existing blocking echo transport is unchanged.
use crate::{WorkerReply, WorkerRequest, MAX_FRAME_BYTES};
use std::io;
use std::process::Stdio;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tokio::time::{timeout, Instant};

const POLL: Duration = Duration::from_millis(5);
const REAP_BUDGET: Duration = Duration::from_secs(1);

#[derive(Debug)]
pub enum WorkerError {
    Busy,
    InvalidDeadline,
    Cancelled,
    Deadline,
    Frame,
    Closed,
    ReapPending,
    Io(io::Error),
    Json(serde_json::Error),
}

impl From<io::Error> for WorkerError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}
impl From<serde_json::Error> for WorkerError {
    fn from(value: serde_json::Error) -> Self {
        Self::Json(value)
    }
}

/// Share this single permit between replacement workers. No waiting spawn queue.
pub fn worker_slot() -> Arc<Semaphore> {
    Arc::new(Semaphore::new(1))
}

async fn read_reply(stdout: &mut BufReader<ChildStdout>) -> Result<WorkerReply, WorkerError> {
    let mut line = Vec::new();
    stdout
        .take(MAX_FRAME_BYTES as u64)
        .read_until(b'\n', &mut line)
        .await?;
    if line.is_empty() {
        return Err(WorkerError::Closed);
    }
    if !line.ends_with(b"\n") {
        return Err(WorkerError::Frame);
    }
    Ok(serde_json::from_slice(&line)?)
}

pub struct SupervisedWorker {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
    permit: Option<ProcessLease>,
}

struct ProcessLease(Option<OwnedSemaphorePermit>);

impl ProcessLease {
    fn release(&mut self) {
        self.0.take();
    }
}

impl Drop for ProcessLease {
    fn drop(&mut self) {
        // A failed reaper start/wait is not confirmed exit. Fail closed rather
        // than release capacity merely because the cleanup owner vanished.
        if let Some(permit) = self.0.take() {
            permit.forget();
        }
    }
}

impl SupervisedWorker {
    pub fn spawn(program: &str, args: &[&str], slot: Arc<Semaphore>) -> Result<Self, WorkerError> {
        let permit = slot.try_acquire_owned().map_err(|_| WorkerError::Busy)?;
        tokio::runtime::Handle::try_current().map_err(|_| WorkerError::Closed)?;
        // Trusted first-party executable/arguments only, never a shell or payload code.
        let mut child = Command::new(program)
            .args(args)
            .env("HF_HUB_OFFLINE", "1")
            .env("TRANSFORMERS_OFFLINE", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().map(BufReader::new);
        Ok(Self {
            child: Some(child),
            stdin,
            stdout,
            permit: Some(ProcessLease(Some(permit))),
        })
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.as_ref().and_then(Child::id)
    }

    pub async fn exchange(
        mut self,
        request: &WorkerRequest,
        cancelled: &AtomicBool,
        deadline: std::time::Instant,
    ) -> Result<(Self, WorkerReply), WorkerError> {
        let reply = self.exchange_owned(request, cancelled, deadline).await?;
        Ok((self, reply))
    }

    async fn exchange_owned(
        &mut self,
        request: &WorkerRequest,
        cancelled: &AtomicBool,
        deadline: std::time::Instant,
    ) -> Result<WorkerReply, WorkerError> {
        if request.deadline_ms == 0 || request.deadline_ms > 120_000 {
            return Err(WorkerError::InvalidDeadline);
        }
        let mut frame = serde_json::to_vec(request)?;
        frame.push(b'\n');
        if frame.len() > MAX_FRAME_BYTES {
            return Err(WorkerError::Frame);
        }
        let deadline = Instant::from_std(deadline)
            .min(Instant::now() + Duration::from_millis(request.deadline_ms));
        let stdin = self.stdin.as_mut().ok_or(WorkerError::Closed)?;
        let stdout = self.stdout.as_mut().ok_or(WorkerError::Closed)?;
        // Retain the same future across polls: cancelling and recreating write_all
        // could otherwise resend a partially written control frame.
        let mut operation = Box::pin(async {
            stdin.write_all(&frame).await?;
            stdin.flush().await?;
            read_reply(stdout).await
        });
        let result = loop {
            if cancelled.load(Ordering::Acquire) {
                break Err(WorkerError::Cancelled);
            }
            if Instant::now() >= deadline {
                break Err(WorkerError::Deadline);
            }
            match timeout(
                POLL.min(deadline.saturating_duration_since(Instant::now())),
                &mut operation,
            )
            .await
            {
                Ok(result) => {
                    if cancelled.load(Ordering::Acquire) {
                        break Err(WorkerError::Cancelled);
                    }
                    if Instant::now() >= deadline {
                        break Err(WorkerError::Deadline);
                    }
                    break result;
                }
                Err(_) => continue,
            }
        };
        drop(operation);
        if result.is_err() {
            // Failed framing/IO is terminal too; never reuse a desynchronised pipe.
            self.stop().await?;
        }
        result
    }

    pub async fn stop(&mut self) -> Result<(), WorkerError> {
        self.stdin.take();
        self.stdout.take();
        if let Some(child) = self.child.as_mut() {
            if child.try_wait()?.is_none() {
                child.start_kill()?;
                match timeout(REAP_BUDGET, child.wait()).await {
                    Ok(Ok(_)) => {}
                    Ok(Err(error)) => return Err(WorkerError::Io(error)),
                    Err(_) => {
                        // Keep the child AND permit: a replacement cannot accumulate
                        // while the operating system has not confirmed the old exit.
                        return Err(WorkerError::ReapPending);
                    }
                }
            }
        }
        self.child.take();
        if let Some(permit) = self.permit.as_mut() {
            permit.release();
        }
        self.permit.take();
        Ok(())
    }
}

impl Drop for SupervisedWorker {
    fn drop(&mut self) {
        self.stdin.take();
        self.stdout.take();
        if let Some(mut child) = self.child.take() {
            let _ = child.start_kill();
            let mut permit = self.permit.take();
            // Cancellation of an exchange future still kills/reaps its owned
            // child. Retain admission until wait completes, even after timeout.
            let _ = std::thread::Builder::new()
                .name("ltf-worker-reap".into())
                .spawn(move || loop {
                    match child.try_wait() {
                        Ok(Some(_)) => {
                            if let Some(permit) = permit.as_mut() {
                                permit.release();
                            }
                            break;
                        }
                        Ok(None) => std::thread::sleep(POLL),
                        Err(_) => break,
                    }
                });
            // Independent of Tokio runtime lifetime. Failed thread start/reap
            // drops ProcessLease without release, permanently retaining capacity.
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unverified_reap_forgets_capacity_instead_of_releasing() {
        let slot = worker_slot();
        drop(ProcessLease(Some(
            slot.clone().try_acquire_owned().unwrap(),
        )));
        assert_eq!(slot.available_permits(), 0);
    }
}
