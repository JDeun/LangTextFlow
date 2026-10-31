pub mod transport;
//! LangTextFlow's transport-neutral worker protocol and stale-result fence.
//! Inference remains owned by Python; this crate never loads ASR/MT models.

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u16 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskKind { Asr, Translation, Correction }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkerRequest {
    pub protocol_version: u16,
    pub request_id: String,
    pub session_id: String,
    pub session_generation: u64,
    pub segment_id: String,
    pub source_revision: u64,
    pub task_kind: TaskKind,
    pub source_language: String,
    pub target_language: Option<String>,
    pub deadline_ms: u64,
    pub payload: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkerStatus { Ok, Error, Cancelled }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkerReply {
    pub protocol_version: u16,
    pub request_id: String,
    pub session_id: String,
    pub session_generation: u64,
    pub segment_id: String,
    pub source_revision: u64,
    pub status: WorkerStatus,
    pub result: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReplyRejection { Version, Identity, StaleGeneration, StaleRevision, InvalidResult }

/// Validate before any result can advance the caption state.
/// The caller must additionally enforce its monotonic deadline and current
/// session generation, rather than treating a worker's clock as authoritative.
pub fn validate_reply(
    request: &WorkerRequest,
    reply: &WorkerReply,
    current_generation: u64,
    current_revision: u64,
) -> Result<(), ReplyRejection> {
    if request.protocol_version != PROTOCOL_VERSION || reply.protocol_version != PROTOCOL_VERSION {
        return Err(ReplyRejection::Version);
    }
    if request.request_id != reply.request_id
        || request.session_id != reply.session_id
        || request.segment_id != reply.segment_id
        || request.session_generation != reply.session_generation
        || request.source_revision != reply.source_revision
    {
        return Err(ReplyRejection::Identity);
    }
    if request.session_generation != current_generation {
        return Err(ReplyRejection::StaleGeneration);
    }
    if request.source_revision != current_revision {
        return Err(ReplyRejection::StaleRevision);
    }
    match reply.status {
        WorkerStatus::Ok if reply.result.is_some() && reply.error.is_none() => Ok(()),
        WorkerStatus::Error if reply.error.is_some() && reply.result.is_none() => Ok(()),
        WorkerStatus::Cancelled if reply.result.is_none() => Ok(()),
        _ => Err(ReplyRejection::InvalidResult),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn pair() -> (WorkerRequest, WorkerReply) {
        let req = WorkerRequest { protocol_version: 1, request_id: "r1".into(), session_id: "s1".into(), session_generation: 2, segment_id: "seg1".into(), source_revision: 3, task_kind: TaskKind::Translation, source_language: "ko".into(), target_language: Some("en".into()), deadline_ms: 5000, payload: "안녕하세요".into() };
        let reply = WorkerReply { protocol_version: 1, request_id: "r1".into(), session_id: "s1".into(), session_generation: 2, segment_id: "seg1".into(), source_revision: 3, status: WorkerStatus::Ok, result: Some("hello".into()), error: None };
        (req, reply)
    }
    #[test] fn accepts_matching_reply() { let (r,p)=pair(); assert_eq!(validate_reply(&r,&p,2,3),Ok(())); }
    #[test] fn rejects_stale_session() { let (r,p)=pair(); assert_eq!(validate_reply(&r,&p,4,3),Err(ReplyRejection::StaleGeneration)); }
    #[test] fn rejects_stale_revision() { let (r,p)=pair(); assert_eq!(validate_reply(&r,&p,2,4),Err(ReplyRejection::StaleRevision)); }
    #[test] fn rejects_identity_mismatch() { let (r,mut p)=pair(); p.request_id="wrong".into(); assert_eq!(validate_reply(&r,&p,2,3),Err(ReplyRejection::Identity)); }
    #[test] fn rejects_protocol_mismatch() { let (r,mut p)=pair(); p.protocol_version=2; assert_eq!(validate_reply(&r,&p,2,3),Err(ReplyRejection::Version)); }
    #[test] fn rejects_invalid_result() { let (r,mut p)=pair(); p.error=Some("error".into()); assert_eq!(validate_reply(&r,&p,2,3),Err(ReplyRejection::InvalidResult)); }
    #[test] fn json_roundtrip() { let (r,p)=pair(); assert_eq!(serde_json::from_str::<WorkerRequest>(&serde_json::to_string(&r).unwrap()).unwrap(),r); assert_eq!(serde_json::from_str::<WorkerReply>(&serde_json::to_string(&p).unwrap()).unwrap(),p); }
}
