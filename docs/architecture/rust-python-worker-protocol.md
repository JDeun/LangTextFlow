# Rust core ↔ Python worker protocol (initial contract)

Tracking: #344. The initial Rust crate is at `crates/langtextflow-core`.

## Current implementation boundary

This first slice defines **serializable request/reply data types and a pure stale-result validation function**. It does **not** yet spawn Python, send IPC frames, capture audio, or replace the existing FastAPI runtime. No E2E wiring is claimed.

Rust's `validate_reply` rejects protocol mismatches, mismatched request/session/segment/revision identities, stale session generations, stale source revisions and invalid result/error combinations. A successful reply is only eligible for further processing; caller-owned deadlines, queue admission and caption commits remain future work.

## Wire format

JSON fields use snake_case. Protocol version starts at 1. The transport framing (length-prefixed stdin/stdout versus local socket), authenticated lifecycle, deadlines and cancellation control messages require a subsequent implementation decision. `payload` is a string placeholder for a typed payload envelope, **not** a production binary audio transport.

```json
{"protocol_version":1,"request_id":"r1","session_id":"s1","session_generation":2,"segment_id":"seg1","source_revision":3,"task_kind":"translation","source_language":"ko","target_language":"en","deadline_ms":5000,"payload":"안녕하세요"}
```

```json
{"protocol_version":1,"request_id":"r1","session_id":"s1","session_generation":2,"segment_id":"seg1","source_revision":3,"status":"ok","result":"hello","error":null}
```

## Next acceptance gates

1. Choose IPC framing and introduce Python worker with version handshake and request correlation.
2. Bound outstanding requests, enforce monotonic deadlines and cancellation; test worker crash/restart.
3. Wire the Rust scheduler to existing Python ASR/MT providers without changing public WebSocket caption semantics.
4. Run `cargo test --manifest-path crates/Cargo.toml --workspace` in Linux/Windows CI, then native multilingual E2E and 4GB/8GB profiling.
