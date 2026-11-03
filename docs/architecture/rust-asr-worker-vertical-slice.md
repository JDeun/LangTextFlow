# Rust-owned ASR worker vertical slice (prototype)

Status: implementation prototype, not a production cutover or field acceptance.

The [user-approved Option A decision](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339)
requires a Rust core with Python inference workers. This is the first real-model
slice toward that target, not evidence that #154–#158 or #344 are complete.
The [architecture audit](../research/hex-rust-streaming-architecture-audit.md)
remains the target ownership reference. Existing production Python caption,
WebSocket, persistence and desktop behavior is preserved during parity work.

The next [canonical caption/WebSocket slice](rust-caption-websocket-prototype.md)
adds an explicit source-only observer mode. Full correction/MT, persistence and
production integration remain separate acceptance work.

## Implemented boundary

`langtextflow-core::asr_slice::AsrSession` owns prototype admission, session
generation, source revision and the accepted source-event projection. One
request is admitted at a time, with no waiting admission queue; at most 128
source chunks are retained per session. A new session or an active source
revision cancels the old admission. An accepted inference window is terminal in
this prototype. Its event stage remains STABLE: this is not the production
STABLE→correction/MT→COMMITTED contract or proof of #156 parity. Dropping an
aborted admission releases only its own logical slot; process admission remains
leased independently until the old child has been reaped.

`supervision::SupervisedWorker` launches the explicitly configured local Python
interpreter, sets offline model environment flags, and enforces a Rust monotonic
1–120,000ms request deadline over both control writes and reply reads, including
lazy model load. The deadline is captured at Rust admission and checked again
under the acceptance lock; a reply received earlier cannot be accepted after
expiry. A cancellation flag is polled at up to 5ms intervals. Failed IO,
framing, cancellation or deadline kills the child and awaits exit with a 1s
cleanup budget. The public `exchange(self, ...)` moves ownership into the future
and returns the worker only on success. An external timeout/aborted caller thus
cannot retain and reuse a live worker with a partially consumed pipe. If reaping
is still pending, the child and capacity permit are retained; a replacement
sharing the slot cannot multiply children. Owner drop starts a single independent
OS reaper thread holding the permit, including outside/after the Tokio runtime.
It releases capacity only after `try_wait` confirms exit. A reaper start/wait
failure permanently retains that slot (fail closed). Await `stop()` for explicit
cleanup; process creation itself is an OS call, not preempted by this
request deadline. Tokio is pinned to 1.53.3, which includes the
[I/O safety patch](https://rustsec.org/advisories/RUSTSEC-2026-0386.html).
The existing process/io/net/time/sync runtime features remain unchanged.

The Python `langtextflow.inference_worker` loads the existing cached
`FasterWhisperStreamingAsrEngine` synchronously, invokes its existing decoder,
VAD/timestamp validation and bounded recovery, and returns immutable
chunk-relative segments. It never creates a CaptionRuntime, translator, event
store, WebSocket or database. It cannot decide which session/revision commits.
The decoder context is fixed to title `ASR prototype`, general preset, no
hotwords/glossary/reference. The shared request model requires a nominal distinct
target language; that field is unused by the ASR decoder and does not cause MT.
This prompt setting must be kept explicit in any quality comparison.

The worker's bounded raw-FD reader also detects parent EOF during native
load/inference and exits without Python buffered-stdin finalization races.
Native logs go to the null device, not protocol stdout. Terminal errors expose
only `asr_worker_failed`, never native paths, endpoint strings or tracebacks.

## Typed v1 control payload

The existing v1 echo fixture/wire fields remain compatible. For this ASR worker,
`task_kind=asr`, `target_language=null`, and `payload` contains the JSON encoding
of a strict `AudioChunk` object:

```json
{"wav_path":"/operator-selected/fixture.wav","start_sample":0,"end_sample":64000,"sample_rate":16000}
```

This is a trusted operator-selected regular WAV descriptor, not an exposed file
service. Raw speech never travels in JSONL. PCM must be mono signed PCM16 at
16kHz; each interval is nonempty and at most 120s. Each file is capped at 128MiB.
Range/short-read/shape checks occur before native inference; models resolve only
through the shared offline cache/local-directory resolver. There is no download
or remote-code path. The input file must remain immutable during the run; this
phase does not implement a durable recording ownership/hash protocol.

The reply `result` is the JSON encoding of `AsrResult` (`sample_count`,
`sample_rate`, typed `segments`). Both UTF-8 JSONL frames are capped at 128KiB
including newline. Rust rechecks protocol/identity/current generation/revision,
sample count/rate, finite segment bounds, text/segment limits and result/error
consistency under its state lock before atomic projection. The existing Whisper
decoder's 250ms padded-timestamp tolerance is preserved, then Rust bounds event
times to admitted PCM and marks clipped estimates explicitly. Absurd timestamps
still fail. `sample_clock_native_hint` is not independently aligned word timing.

## Explicit invocation (cached-only)

```bash
export PYTHONPATH=apps/server
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cargo run --locked --manifest-path crates/Cargo.toml \
  --example asr_worker_slice -- \
  /path/to/existing/python /path/to/approved.wav \
  /path/to/prepared/whisper-snapshot /path/to/existing/cache 2 ko 199680
```

Arguments are interpreter, WAV, prepared model/alias, cache, CPU threads, source
language and total PCM frame count. The example supplies four-second chunks and
prints only accepted Rust source-event JSON with `field_ready=false`. It awaits
owned-worker cleanup on success and error. Use the real WAV frame count, not the
example number; maximum retained chunks are 128. No API/browser/desktop route
selects this prototype. The worker is source-mode only; frozen dispatch is not
claimed by this slice.

## Verification and remaining handoff

Rust tests use real OS children for hung load/inference-like work, blocked writes,
deadline/cancel, process-capacity rejection, oversized output/crash, kill/reap,
owner abort, restart and stale-result rejection. Python subprocess tests exercise
the actual shipped worker/cache/decoder code with synthetic external native
dependencies, including load failure, parent EOF during hung load/inference and
non-disclosure of native exceptions. These are lifecycle tests, not speech scores.

Still required before production handoff: Rust audio/VAD/sample-clock admission,
continuous-source/replay parity, native capability/provenance and context/hotword
parity, full event schema, target/correction scheduling, actual WebSocket observers,
persistence, desktop/frozen dispatch and rollback. Network/UI/persistence do not
yet consume this prototype projection. Native quality, real microphone failover,
six-direction MT, licensed references and physical 4GB/8GB/long-soak acceptance
remain independent. No Python-to-Rust main-path cutover is authorized by passing
these tests alone.
