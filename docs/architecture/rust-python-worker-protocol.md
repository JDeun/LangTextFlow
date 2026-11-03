# Experimental Rust ↔ Python worker protocol scaffold

Tracking: [#344](https://github.com/JDeun/LangTextFlow/issues/344). This is an optional
research scaffold, not completion evidence for runtime issues #154/#155/#156.

## Implementation boundary

`crates/langtextflow-core` defines versioned request/reply types, a stale-result
validator, and a synchronous Rust child-process JSONL transport. The Python
`langtextflow.worker_ipc` process is a deterministic **echo fixture**. The task names
`asr`, `translation`, and `correction` do not invoke models. Nothing in the FastAPI
runtime, desktop shell, or caption pipeline calls this crate or fixture.

The production runtime continues using the existing Python pipeline, bounded
queues, per-target status, source-revision checks and session-generation fence.
This scaffold does not replace those paths, prove speech quality, or qualify any
4GB/8GB CPU target.

## Wire format and boundaries

Each frame is a UTF-8 JSON object plus newline, at most **128 KiB including the
newline** on both sides. Rust rejects an oversized request before writing it and
bounds reply reads. The Python fixture bounds input/output and exits after an
oversized or unterminated input frame. After a framing error, the parent must drop
and restart the worker; it must not reuse a potentially desynchronized stream.
`payload` is a text placeholder, not an audio or production model transport.

```json
{"protocol_version":1,"request_id":"r1","session_id":"s1","session_generation":2,"segment_id":"seg1","source_revision":3,"task_kind":"translation","source_language":"ko","target_language":"en","deadline_ms":5000,"payload":"안녕하세요"}
```

```json
{"protocol_version":1,"request_id":"r1","session_id":"s1","session_generation":2,"segment_id":"seg1","source_revision":3,"status":"ok","result":"안녕하세요","error":null}
```

The echo fixture never evaluates payloads, starts a shell, or loads a provider.
The parent must supply a trusted interpreter/executable and arguments. This is a
local child process, with no network listener or standalone authentication layer.
Stdout contains protocol frames only; diagnostics belong on stderr.

`validate_reply` checks version, request/session/segment identity, generation,
source revision and result/error combinations. Callers must supply current state
and recheck it before committing a result; values captured before a blocking
exchange cannot establish that a later result is still current.

`deadline_ms` is currently metadata. `exchange` can block indefinitely on a hung
worker. There is no cancellation command, multiplexing, handshake, scheduler,
queue admission, model provisioning or automatic restart. Dropping a Rust worker
kills and reaps its child; a Python parent can terminate/reap the idle fixture.
This process-lifetime cleanup does not prove cancellation of native inference.

## Checks and remaining gates

CI runs the standalone crate on Linux and Windows with Rust 1.90.0 and a committed
lockfile. Rust tests cover validation, actual Python subprocess exchange, stale
revision rejection, worker exit, oversized requests and bounded oversized replies.
Python tests cover malformed JSON, unsupported requests, literal executable-looking
payloads, invalid UTF-8, oversized frames and parent-owned termination/restart.

Before any production caller is added, implement bounded supervision with monotonic
deadlines, cancellation and restart, typed model payloads, and actual provider
invocation. Then verify stop/restart and in-flight revision arbitration through the
real caption state/HTTP/WebSocket paths. Native multilingual E2E and CPU/RAM/latency
qualification remain separate acceptance gates. This scaffold does not authorize a
Rust runtime migration or close existing architecture/quality issues.
