# Hex source audit and LangTextFlow Rust-core decision

Status: research record; implementation and native benchmarks pending  
Date: 2026-10-31  
Decision: **Rust orchestration core + Python ASR/MT/LLM model workers (Option A)**  
Tracking: [#344](https://github.com/JDeun/LangTextFlow/issues/344)

## Scope and evidence

Inspected upstream `anomalyco/hex` on branch `dev` through GitHub source fetch, not a local clone. Key blob IDs:

| Upstream source | Blob SHA | Findings |
| --- | --- | --- |
| `Cargo.toml` | `af6761e004ec0d1011558781e0e2d9729445cce7` | Root package monolith, `crates/router` workspace member, platform dependencies, patched transcribe.cpp |
| `src/transcription_stream.rs` | `c32ef9de03abaf84ffc37c622678f4cf2e10ffb0` | Admission, backlog, lease, stream worker, fallback |
| `src/transcription_engine.rs` | `8bb28619a14ef568e07a34fc7f2bbc2571ba3776` | Single model-owning thread, priority, cancellation, deadlines |
| `src/local_api/streams.rs` | `3ec0c8b21f03a4c0d8258a669f7d5b12e840ad5c` | HTTP routes, per-stream ownership token, tests |
| `src/transcription_stream_tests.rs` | `2c369b5ee05a5acc4ed294b10fd976e5bdf0d20f` | Mock-engine stream failure/priority tests |
| `src/pause_segmenter.rs` | `c2f84e328922a42ff23539e9b8cd214c92113686` | Adaptive noise floor, pause splitting |

Reference documents: [streams API](https://github.com/anomalyco/hex/blob/dev/docs/streams.md), [embedded service spec](https://github.com/anomalyco/hex/blob/dev/docs/specs/local-transcription-service.md), [agent architecture](https://github.com/anomalyco/hex/blob/dev/AGENTS.md), [third-party notices](https://github.com/anomalyco/hex/blob/dev/THIRD_PARTY_NOTICES.md).

**Evidence limitation:** blob IDs pin individual files, not a complete repository snapshot. No cargo build, test execution, native E2E, dependency-license scan, or CPU/RAM measurement was performed.

## Verified streaming call path

`POST /streams/{id}/audio` → `Stream::push` → `Worker::transcribe` → `Worker::take` → `PauseSegmenter::push` → `Worker::feed` → `Engine::begin` / `Live::feed` → `Worker::publish_partial` → `Worker::close_window` → live finalize **or** `Engine::transcribe` offline fallback → `Segment` / `End`.

When dictation has priority, pending audio remains bounded; a yielding stream may be preempted, and the window can be replayed via offline inference. An engine model change terminates the stream rather than silently continuing with a different model.

## Contracts and limits

- 16 kHz mono PCM, `f32le` or `s16le`; non-finite floats rejected.
- One live stream; per-stream ownership token plus API bearer authentication.
- 256 KiB per audio request, 120 seconds of unprocessed audio, 25-second maximum transcription window.
- 10-second stream lease; 120-second HTTP finish wait; 0.4-second maximum in-flight audio; 0.1-second minimum feed.
- Events: `ready`, `partial` (`stable` and `tentative`), `segment`, `final`, `error`, `cancelled`. No word timestamps in this API.
- The live `/streams` API is presently hosted by the running macOS app; Linux/Windows dictation support does **not** imply those platforms expose this API.
- Engine job deadline: 180 seconds plus 8 seconds per audio second, starting **after** job execution begins; shutdown waits up to 10 seconds.
- Pause segmenter: 20 ms frames, adaptive noise floor, 12 dB speech margin, -55 dBFS minimum speech threshold; split after at least 3 seconds and 600 ms pause, or 300 ms pause after 8 seconds.

These are **upstream defaults**, not LangTextFlow acceptance targets.

## Failure behavior and existing upstream tests

The source includes tests for stream preemption/restart, backlog catch-up, offline fallback, non-streaming models, exclusive admission, lease expiry, backlog refusal, model change, route validation and ownership. Engine tests cover hung jobs, queue wait excluded from execution deadline, cancellation isolation, and shutdown. **Test presence is not evidence of passing tests.**

Risk: a 120-second backlog is incompatible with a low-latency live-caption SLO without an explicit freshness policy. Offline replay can stall translation. Single source microphone and multiple audience connections must be modeled independently.

## Reuse matrix

| Component | Decision | Rationale |
| --- | --- | --- |
| Pause splitting and noise-floor policy | Reimplement/adapt with tests | Small deterministic logic; validate sermon reverberation and music |
| Bounded queue, priority, preemption | Adopt design | Relevant to Rust scheduler; avoid Hex app ownership assumptions |
| Cancellation/deadline semantics | Adopt design, add generation fences | Python processes cannot rely on native Rust `CancelToken` |
| Partial/segment event concepts | Map explicitly | Hex ASR `stable` is not LangTextFlow translated-caption `COMMITTED` |
| Hex `transcription_engine` native model thread | Do not transplant | A-option model ownership stays in Python workers |
| Hex desktop UI, OS hooks, native GGUF stack | Exclude | Unnecessary dependencies and cross-platform coupling |
| Local API bearer/owner tokens | Adapt threat model | Browser audience auth differs from loopback process API |

## Proposed LangTextFlow boundary

**Rust owns:** audio admission/VAD, session lifecycle, bounded queues, deadlines, worker supervision, source revision fencing, caption state transitions, WebSocket broadcast, eventual persistence.

**Python owns:** ASR/MT/LLM inference and model load/warmup/release. Workers return data, never authoritatively commit caption state.

Versioned worker IPC request fields: `protocol_version`, `request_id`, `session_id`, `session_generation`, `segment_id`, `source_revision`, `task_kind`, `source_language`, `target_language`, `deadline_ms`, and typed payload. Replies echo identity and include `status`, `result` or structured error. Rust rejects stale generation/revision, enforces deadlines, and makes commit decisions. IPC transport and backpressure policy must be selected in the RFC, not assumed.

## Acceptance gates and open risks

1. Verify exact upstream commit and transitive licenses/model-weight terms before copying any code; Hex MIT does not automatically license every dependency or model.
2. Implement a minimal Rust↔Python worker vertical slice with schema version negotiation, cancellation, stale-result rejection, worker restart, queue saturation and crash recovery.
3. Preserve existing LangTextFlow `PARTIAL/STABLE/COMMITTED` and WebSocket/API behavior with regression tests.
4. Run Linux and Windows CPU-only tests, including 4GB/8GB memory budgets; measure p50/p95 caption latency, RSS, queue depth and recovery time.
5. Benchmark Korean/English/Japanese real sermon audio with silence, reverberation, music, code switching and glossary terms.
6. Establish freshness and audio-drop/fallback policy instead of copying Hex's 120-second backlog uncritically.

## Decision

**Proceed with a clean LangTextFlow Rust orchestration core and existing Python model workers.** Hex is a reference implementation for scheduler/admission/test patterns, **not** a direct whole-crate dependency. Native compatibility, performance and license audits remain explicit release gates.
