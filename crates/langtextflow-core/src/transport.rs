//! JSON-lines IPC over a child process' stdin/stdout.
//! One request at a time. A production supervisor must add process-level
//! timeouts, cancellation, bounded queues and restart semantics.
use crate::{validate_reply, ReplyRejection, WorkerReply, WorkerRequest, MAX_FRAME_BYTES};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

#[derive(Debug)]
pub enum TransportError {
    Io(io::Error),
    Json(serde_json::Error),
    Rejected(ReplyRejection),
    MissingPipe,
    FrameTooLarge,
}

impl From<io::Error> for TransportError {
    fn from(e: io::Error) -> Self {
        Self::Io(e)
    }
}
impl From<serde_json::Error> for TransportError {
    fn from(e: serde_json::Error) -> Self {
        Self::Json(e)
    }
}

pub struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl WorkerProcess {
    pub fn spawn(program: &str, args: &[&str]) -> Result<Self, TransportError> {
        let mut child = Command::new(program)
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()?;
        let stdin = child.stdin.take().ok_or(TransportError::MissingPipe)?;
        let stdout = BufReader::new(child.stdout.take().ok_or(TransportError::MissingPipe)?);
        Ok(Self {
            child,
            stdin,
            stdout,
        })
    }

    pub fn exchange(
        &mut self,
        request: &WorkerRequest,
        generation: u64,
        revision: u64,
    ) -> Result<WorkerReply, TransportError> {
        let frame = serde_json::to_vec(request)?;
        if frame.len() >= MAX_FRAME_BYTES {
            return Err(TransportError::FrameTooLarge);
        }
        self.stdin.write_all(&frame)?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()?;
        let mut line = Vec::new();
        if self
            .stdout
            .by_ref()
            .take(MAX_FRAME_BYTES as u64)
            .read_until(b'\n', &mut line)?
            == 0
        {
            return Err(TransportError::Io(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "worker closed stdout",
            )));
        }
        if !line.ends_with(b"\n") {
            if line.len() == MAX_FRAME_BYTES {
                return Err(TransportError::FrameTooLarge);
            }
            return Err(TransportError::Io(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "worker frame missing newline",
            )));
        }
        let reply: WorkerReply = serde_json::from_slice(&line)?;
        validate_reply(request, &reply, generation, revision).map_err(TransportError::Rejected)?;
        Ok(reply)
    }
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{TaskKind, WorkerStatus};
    use std::path::Path;

    fn python() -> String {
        std::env::var("LTF_WORKER_PYTHON").unwrap_or_else(|_| "python".into())
    }

    fn worker() -> WorkerProcess {
        let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../apps/server/langtextflow/worker_ipc.py");
        WorkerProcess::spawn(&python(), &[fixture.to_str().unwrap()]).unwrap()
    }

    fn request() -> WorkerRequest {
        WorkerRequest {
            protocol_version: 1,
            request_id: "r1".into(),
            session_id: "s1".into(),
            session_generation: 2,
            segment_id: "seg1".into(),
            source_revision: 3,
            task_kind: TaskKind::Translation,
            source_language: "ko".into(),
            target_language: Some("en".into()),
            deadline_ms: 5000,
            payload: "안녕하세요\nHello".into(),
        }
    }

    #[test]
    fn actual_python_jsonl_roundtrip_preserves_unicode_and_identity() {
        let mut worker = worker();
        let request = request();
        let reply = worker.exchange(&request, 2, 3).unwrap();
        assert_eq!(reply.status, WorkerStatus::Ok);
        assert_eq!(reply.result, Some(request.payload));
        assert_eq!(reply.session_generation, 2);
        assert_eq!(reply.source_revision, 3);
    }

    #[test]
    fn actual_worker_reply_is_rejected_after_revision_change() {
        let mut worker = worker();
        assert!(matches!(
            worker.exchange(&request(), 2, 4),
            Err(TransportError::Rejected(ReplyRejection::StaleRevision))
        ));
    }

    #[test]
    fn frame_at_exact_limit_roundtrips_but_one_extra_byte_is_rejected() {
        let mut worker = worker();
        let mut request = request();
        request.payload.clear();
        let overhead = serde_json::to_vec(&request).unwrap().len() + 1;
        request.payload = "x".repeat(MAX_FRAME_BYTES - overhead);
        assert_eq!(
            worker.exchange(&request, 2, 3).unwrap().result,
            Some(request.payload.clone())
        );
        request.payload.push('x');
        assert!(matches!(
            worker.exchange(&request, 2, 3),
            Err(TransportError::FrameTooLarge)
        ));
    }

    #[test]
    fn oversized_request_is_rejected_before_writing_to_worker() {
        let mut worker = worker();
        let mut oversized = request();
        oversized.payload = "x".repeat(MAX_FRAME_BYTES);
        assert!(matches!(
            worker.exchange(&oversized, 2, 3),
            Err(TransportError::FrameTooLarge)
        ));
        assert_eq!(
            worker.exchange(&request(), 2, 3).unwrap().status,
            WorkerStatus::Ok
        );
    }

    #[test]
    fn oversized_unterminated_worker_output_cannot_allocate_without_bound() {
        let mut worker = WorkerProcess::spawn(&python(), &[
            "-u", "-c", "import sys,time; sys.stdout.write('x'*131072); sys.stdout.flush(); time.sleep(120)",
        ]).unwrap();
        assert!(matches!(
            worker.exchange(&request(), 2, 3),
            Err(TransportError::FrameTooLarge)
        ));
        // Drop kills/reaps the sleeping child instead of waiting for its 120-second sleep.
    }

    #[test]
    fn worker_exit_is_reported_instead_of_waiting_for_a_reply() {
        let mut worker = WorkerProcess::spawn(&python(), &["-c", "pass"]).unwrap();
        assert!(matches!(
            worker.exchange(&request(), 2, 3),
            Err(TransportError::Io(_))
        ));
    }
}
