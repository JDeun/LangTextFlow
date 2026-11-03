# Rust canonical caption and WebSocket prototype

This is the second opt-in slice toward the [approved Rust-core target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339),
following [the real ASR-worker slice](rust-asr-worker-vertical-slice.md). Rust now
owns source-only canonical events, their bounded projection, authorized snapshots
and the actual WebSocket server. No Python relay owns or forwards this observer.
The existing production Python runtime/HTTP/UI remains unchanged.

## Source-only commit contract

`AsrSession::start_observed` chooses an explicit KO/EN/JA source and distinct
targets, and generates a fresh OS-random per-session observer credential. Rust
accepts the existing cached Whisper worker's immutable result only after the
generation, source revision, sample bounds, cancellation and deadline checks.
It publishes full `TranscriptEvent`-compatible STABLE version 1 then COMMITTED
version 2 events. Source text is immutable `original_text`, ASR provenance retains
PCM chunk bounds and native-hint/estimated timing, and unavailable model identity
stays null. No local model path, endpoint or credential enters event metadata.

This mode performs **no correction or translation**. Every selected target is
explicitly `unsupported`, and translations remain empty. This is a legitimate
source-only completion, not six-way interpretation or parity with the production
correction/MT revision pipeline. Model hints are not word alignment. Native
Whisper remains chunked, STABLE-only, with the documented fixed prototype prompt.
The later [explicit MT scheduling slice](rust-mt-scheduling-prototype.md) adds
independent target completion without changing this source-only mode.

Public source IDs use `rs-<Rust source slot>-<segment ordinal>`, within the shared
128-character citation alphabet. A slot survives an uncommitted source revision;
another source with identical speech gets a distinct slot. Arbitrary input chunk
IDs are not truncated, interpolated into public citation IDs, or substituted into
spoken text. The session/generation/revision fence still binds the worker reply.

## Observer boundary

- Bind is IPv4 loopback only. An exact configured local HTTP/HTTPS Origin and the
  current session's 256-bit credential are mandatory. Old credentials cannot
  reconnect after a session change; accept rechecks authority before registering.
- Snapshot enqueue and registration use the same Rust authority lock as event
  publication. The first message is always the authorized current snapshot.
- Frames carry current session/generation and per-session serial metadata. A
  reconnect gets the latest version per segment, never a historical stable
  revision or the previous session's text.
- Projection retains up to 100 segments and at most 1MiB of serialized snapshot.
  Snapshot eviction counts disclose that this is a bounded live view, not an
  archive. Display eviction does not rewrite in-flight sources. Completed chunks
  compact to identity/revision tombstones; caller-retained PCM and durable
  recording evidence are separate from the ephemeral inference spool.
- Each observer has an 8-frame queue. Publication uses `try_send`, never awaits
  transport, and disconnects overflowed observers for snapshot resynchronization.
  This includes producer bursts exceeding the queue, not only slow networks.
  Writes have a 250ms budget and the WS write buffer is capped. Source admission
  and other observers continue independently.
- At most 16 sockets per listener, including pending handshakes; at most 16
  authorized subscribers per authority. Unauthenticated HTTP headers are capped
  at 16KiB and two seconds before the maintained WS parser receives them. Incoming
  frames/messages are capped at 4KiB; an observer cannot submit transcript/control
  commands. Ping/pong and close are handled by the WS library.
- A source transition closes old observers with 1012. Listener shutdown closes
  only that listener's clients; late cleanup of an old listener cannot close a
  replacement listener's subscriptions. Shutdown is bounded and aborts stalled
  handshakes. A stop may discard queued transport frames; it is not disk durability
  or guaranteed delivery after the service exits.

## Explicit cached-model invocation

The existing example's first seven arguments remain supported. Optional last
arguments enable the observer and write credentials to a **new**, private file:

```bash
export PYTHONPATH=apps/server
cargo run --locked --manifest-path crates/Cargo.toml --example asr_worker_slice -- \
  /path/to/existing/python /path/to/approved.wav \
  /path/to/prepared/whisper /path/to/existing/cache 2 ko 199680 \
  0 http://127.0.0.1:5173 /private/operator-temp/observer.json
```

The file contains the loopback URL/token and required Origin; it is created with
0600 mode on Unix and is never overwritten. Keep it private, do not publish it
with receipts, and remove the caller-owned file after the run. Credentials become
invalid when the source session/service ends. No credential appears in event
stdout. Clients may connect while decoding runs; the listener ends with the
bounded fixture run. At most 128 four-second windows are supported by this example.

No models are downloaded. Port 0 selects an available loopback port. This source
prototype does not add a production operator API, frozen/desktop dispatch, Tauri
Origin policy or a browser microphone path.

## Dependencies and remaining acceptance

`tokio-tungstenite=0.30.0` (MIT, declared MSRV 1.85) and its
`tungstenite=0.30.0` backend (MIT OR Apache-2.0, MSRV 1.85) are pinned in Cargo.lock.
Only server handshake support is enabled; TLS/client-connect defaults are off.
The official tag commits match downloaded package VCS metadata. Rust 1.90
compilation is checked; the existing CI runs Linux and Windows. See the immutable
[dependency record](../knowledge/raw/2026-11-03-rust-websocket-dependency.json) and
[upstream notices](../../crates/langtextflow-core/THIRD_PARTY_NOTICES.md).

Actual-socket regressions cover snapshot ordering/reconnect, full event shape,
unsupported targets, source-ID/revision fencing, cross-session credential denial,
read-only ingress, slow-client eviction, header/admission bounds and listener
ownership/shutdown. Synthetic inference is labeled separately from cached native
speech tests. No test certifies semantic translation or independently aligned
word timestamps.

The [continuous PCM slice](rust-continuous-pcm-ingress.md) adds bounded Rust input
and monitoring-VAD/sample-clock ownership. Still required: production capture routing, correction and
MT workers/scheduling/partial-target commits, durable persistence/history, full
operator/audience/desktop integration and rollback parity. The approved target
architecture and model/device/field gates remain open; this observer does not
replace those requirements.
