# Rust continuous PCM ingress prototype

Status: opt-in implementation toward the required [Rust-core target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339),
not a production HTTP/microphone/desktop cutover. This extends the existing
[ASR worker](rust-asr-worker-vertical-slice.md), [canonical WebSocket](rust-caption-websocket-prototype.md)
and [independent-target MT](rust-mt-scheduling-prototype.md) slices.

## Input, VAD and source time

`PcmIngress` binds a fresh existing `AsrSession` generation; its `PcmInput` accepts
ordered mono 16kHz little-endian float32 frames. Empty, misaligned, over-1MiB,
NaN/Inf and absolute amplitudes above 4 are rejected before sample-clock mutation.
One producer admission lease prevents an additional unbounded waiting queue.
Eight raw frames provide backpressure; an awaiting feed cancelled before capacity
is reserved admits no late frame. Consumer failure/stop releases blocked feeds.

Rust mirrors Python `EnergyVad`: RMS dBFS with a -120 floor, -45 threshold and
three **input-frame** hangover. It is a signal meter, never speech classification
or audio dropping. Silence remains in the PCM clock and in every decode window.
The unchanged Python inference worker still owns native Whisper VAD/no-speech
behavior and timestamp recovery. No Rust neural-VAD replacement is claimed.

Rust splits accepted input into four-second/64,000-sample windows and a final
short tail. A sender wall-clock pause adds no samples; actual silent samples do.
The first frame's monotonic arrival time supplies a 120s window budget including
backpressure, partial-window wait, queueing, staging and native load/inference.
The existing authority lock checks generation/revision/cancellation/deadline
before publication. Old queued input cannot acquire a replacement session's ID.

Each private WAV contains only its local window at file offset zero. The existing
v1 descriptor/result schema is unchanged. A small Rust admission boundary maps
that file interval to the original **global** integer PCM interval; no invented
leading silence or mutable session-sized WAV is supplied to inference. Caption
provenance retains global chunk samples, native-hint/estimated timing and unknown
model identity. These timestamps remain estimates, not independent word alignment.

## Quantization and resource bounds

The current worker accepts PCM16 WAV, whereas the live Python adapter accepts
float32 directly. Rust therefore uses the existing recording conversion: clip
to [-1,1], multiply by 32767, round ties to even, encode little-endian signed16.
This changes amplitudes; it is **not byte-identical native float-input parity**.
Clipped-sample counts are exposed separately. Do not compare recognition scores
against a differently quantized input as if their PCM bytes were identical.

| Bound | Scope |
|---|---|
| 8 × 1MiB | Queued raw frames |
| One active frame + one 256KiB window | Consumer/segmentation buffer |
| One 128KiB PCM16 window plus 44-byte header | Private inference spool |
| 128KiB | Existing worker control/reply frame |
| 100 captions / 1MiB serialized snapshot | Existing live observer projection |
| 32,768 public IDs/version records | Existing per-session history cap |
| 2,048 source chunks | Fixed admission/tombstone cap: 136m32s at four seconds |

The source bound supports 30/60/90-minute inputs; independently reaching the
public-ID quota still fails explicitly. Terminal source chunks retain their
identity/revision fence while freeing their duplicate source-event payloads.
Precommit chunks retain the source/ID set needed for revision invalidation.
At maximum source-ID length, the two 2,048-key maps contain at most 512KiB of
key UTF-8, plus fixed entries/container overhead. Raising the cap without
compaction could retain up to 256MiB of full native reply envelopes. Model RAM,
allocator overhead and in-flight MT have separate bounds; this is no physical
4GB/8GB qualification. Old terminal IDs are never reused or made revisable.

## Ownership and stop

One persistent ASR `SupervisedWorker` uses its own shared process slot. Replacement
owners must reuse that slot. Generation changes also terminate idle old owners;
producer clones cannot keep an obsolete model alive. ASR failure closes input,
preserves accepted-versus-processed counters and requires an explicit fresh session.

`finish(self)` closes admission, drains within ten seconds, flushes the final tail
and confirms native reap/spool removal. A drain timeout fails and aborts its owned
task. An external timeout dropping `finish` also drops the owner, not a detached
borrowed operation. `cancel(self)` discards pending input and confirms cleanup;
the metrics identify cancellation and unprocessed accepted samples. Full session
stop additionally belongs to `AsrSession` and the separate MT/observer owners.

Private files use create-new names, a random directory and Unix 0700/0600 modes;
Windows inherits the caller's temporary-directory ACL. Cleanup never recursively
deletes unknown files. Windows delayed file removal waits for that **specific**
child's irreversible reap signal, not a shared permit that a new child may acquire.
Explicit cleanup has a two-second confirmation budget and errors if unconfirmed.
Underlying local filesystem/process-creation syscalls are not preempted by async
budgets; this prototype does not certify storage latency or disk durability.

## Example and verification

The source example reads a caller-owned raw float32 file in 100ms frames, using
the same public producer API a later capture route can call. It does not reread
an operator-selected WAV per decode or select a production backend:

```bash
export PYTHONPATH=apps/server
cargo run --locked --manifest-path crates/Cargo.toml --example pcm_ingress_slice -- \
  /existing/asr-python /approved/mono16k.f32le /prepared/whisper /existing/cache 2 ko \
  0 http://127.0.0.1:5185 /private/new-observer.json \
  /existing/mt-python /prepared/m2m100-ct2 /prepared/m2m100-tokenizer 2
```

Omit the final four arguments for the existing source-only/unsupported-target
mode. MT context remains general/glossary off; ASR retains its fixed prototype
prompt. Models resolve offline; no download or cloud fallback is performed.
The private observer file is caller-owned. `input_drained` reports PCM processing,
not successful translations, semantic quality or guaranteed delivery at shutdown.

Actual OS-child/WebSocket regressions cover variable frames, silence/pause,
invalid input, final tail, global timing, backpressure/cancel, source revision,
active/idle session restart, external finish cancellation, drain timeout and MT
partial commits. A **synthetic fast replay** crosses 128→129 and processes 1,351
four-second windows (90m4s of PCM clock), checks the bounded latest snapshot and
rejects revision of the oldest terminal source. This is neither a real-time native
soak nor a model-speed result. Cached native receipts must separately record input
and quantization hashes, estimates, model-file identity and cleanup evidence.

Still required: production capture/audio routing, context/hotword parity,
correction/persistence integration and rollback parity. Native semantic quality,
microphone/device, controlled latency and long native soak gates remain separate.
