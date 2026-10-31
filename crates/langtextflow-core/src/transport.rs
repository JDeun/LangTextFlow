//! JSON-lines IPC over a child process' stdin/stdout.
//! One request at a time. A production supervisor must add process-level
//! timeouts, cancellation, bounded queues and restart semantics.
use crate::{validate_reply, ReplyRejection, WorkerReply, WorkerRequest};
use std::io::{self, BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

#[derive(Debug)]
pub enum TransportError {
    Io(io::Error),
    Json(serde_json::Error),
    Rejected(ReplyRejection),
    MissingPipe,
}

impl From<io::Error> for TransportError { fn from(e: io::Error) -> Self { Self::Io(e) } }
impl From<serde_json::Error> for TransportError { fn from(e: serde_json::Error) -> Self { Self::Json(e) } }

pub struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl WorkerProcess {
    pub fn spawn(program: &str, args: &[&str]) -> Result<Self, TransportError> {
        let mut child = Command::new(program).args(args).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn()?;
        let stdin = child.stdin.take().ok_or(TransportError::MissingPipe)?;
        let stdout = BufReader::new(child.stdout.take().ok_or(TransportError::MissingPipe)?);
        Ok(Self { child, stdin, stdout })
    }

    pub fn exchange(&mut self, request: &WorkerRequest, generation: u64, revision: u64) -> Result<WorkerReply, TransportError> {
        // The protocol is bounded by the caller: do not send untrusted or
        // arbitrarily large model payloads through the JSON-lines control plane.
        serde_json::to_writer(&mut self.stdin, request)?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()?;
        let mut line = String::new();
        if self.stdout.read_line(&mut line)? == 0 {
            return Err(TransportError::Io(io::Error::new(io::ErrorKind::UnexpectedEof, "worker closed stdout")));
        }
        let reply: WorkerReply = serde_json::from_str(&line)?;
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
