# Rust routing through the existing application API

Status: intermediate opt-in application wiring toward the **required**
[Rust core + Python inference target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339).
Python remains the default and rollback path. This is not completion of the
architecture, desktop/frozen packaging, model selection or field qualification.

## One application path, one caption authority

`LANGTEXTFLOW_RUNTIME_CORE=rust` makes the existing `CaptionRuntime` construct a
`RustRuntimeBridge`. The current operator start/stop/state, browser microphone
`/ws/audio`, captions, audience join/WS and history/export APIs stay in use.
Existing operator loopback/Origin checks, audience authorization, single audio
producer claim and expected-session guards remain at those same boundaries.
There is no new public control API, UI panel or model download.

The trusted server starts the explicitly configured `langtextflow-runtime`
executable, or the bundled `runtime/langtextflow-runtime[.exe]` in frozen builds,
without a shell. Python supplies the existing session metadata and
initializes/migrates the existing SQLite schema. Rust owns the accepted session
generation, continuous PCM/VAD, ASR worker, bounded independent-target MT,
canonical revision/commit projection, authorized observer and caption-history
writes. Inference workers receive immutable existing requests, not database paths.
Python's pipeline and caption persistence queue are bypassed. Its CaptionStore
and per-client hub are UI relay mirrors; they neither transform source text nor
create new caption revisions. Rust event timestamps/provenance survive unchanged.

The supported subset is cached CPU/int8 faster-whisper in four-second windows,
KO/EN/JA source with one or two distinct targets, deterministic correction with
optional explicitly selected Ollama correction and independent audio recording,
any existing preset with source-scoped glossary/hotwords and ASR context hints,
and none or cached M2M100 CT2 MT. Display/audience settings remain available.
Unsupported combinations, incompatible chunk/VAD/device settings
and a missing executable are refused **before database initialization or live
session mutation**. There is no silent fallback or omitted requested feature.
The fixed 100-segment live projection is also checked against server configuration.
Unavailable cached model/runtime initialization can still fail a valid start;
that failed attempt is ended through existing bootstrap cleanup.

Known public Whisper aliases describe configured identity only. Explicit local
directories never become public model identities, including relative paths that
look like repository IDs. Verified model/revision stays unknown; no model hashing,
checkpoint verification, inferred word alignment or historical rewrite is added.

## Private transport and ownership bounds

| Boundary | Bound / contract |
|---|---|
| Bootstrap | One UTF-8 JSON line, 64KiB including newline; strict Rust fields; executable/argument/path bounds |
| Inference context | Immutable ≤48KiB UTF-8 JSON snapshot, validated before source transition; the existing 128KiB worker envelope remains unchanged |
| Audio/control | One-byte tag + little-endian u32 length; audio ≤1MiB and float32-aligned; end/stop have zero payload |
| Control queue | One pending command; excess/malformed input closes authority admission before payload allocation |
| Control output | ≤1KiB JSON replies; one queued writer operation; one-second async write confirmation |
| Parent ACK | Session ID, generation and ordered sequence checked; ≤15s startup reply / ≤15s + max(configured correction, target budget) exchange |
| Correction | One supervised Python child, four queued jobs and one active job; immutable shared context; source publication never awaits it |
| Observer | Existing loopback-only random port, private random token, exact internal Origin; bounded handshake/client/FIFO/write limits |
| PCM / MT / history | Existing [PCM bounds](rust-continuous-pcm-ingress.md), [MT scheduler](rust-mt-scheduling-prototype.md), [SQLite writer](rust-sqlite-history.md) |

The observer token appears only in the owned readiness pipe and internal WS
handshake, never command-line arguments, public state, caption events or logs.
The Python WS URL is built solely from a validated integer loopback port and the
strict observer path; proxy discovery is disabled. No redirects, remote endpoint
configuration or ambient credentials enter that connection.

An accepted command has one independent owned ACK task. HTTP/audio-socket waiter
cancellation cannot leave a stale reply for another command. End/stop waits until
the relay has observed the Rust publication serial, so final accepted captions
reach the existing mirror before its connections close. This is relay delivery,
not browser paint or audience acknowledgement. A normal browser audio disconnect
retains the existing session semantics; it does not automatically start another
source or backend. Loss of the private observer fails the source and closes the
parent pipe rather than continuing with an incomplete mirror.

On Windows source virtualenvs, inference uses the base Python executable with a
fixed trusted venv-site bootstrap. Rust therefore owns the actual interpreter
PID instead of only the virtualenv redirector. `-S` prevents base/global site
packages from taking precedence over the trusted virtualenv's package versions.
No user transcript or remote code becomes executable code.

Frozen workers use fixed ASR/correction/MT entrypoint flags on the same sidecar
executable. The Rust owner inherits the frozen runtime environment and sets the
public `PYINSTALLER_RESET_ENVIRONMENT=0` option for workers; it does not rewrite
private bootloader variables. Packaging pins PyInstaller 6.22.3, whose
[upstream fix](https://github.com/pyinstaller/pyinstaller/issues/9513) supports
an intervening native process between frozen instances. Version 6.22.2 required
the immediate parent to use the same executable, which is incompatible with
the Rust worker owner. The project license and existing Rust third-party notices
are embedded under `licenses/`; this is not a complete release SBOM or license
review. The external frozen smoke records actual process ownership and lifecycle;
its model-free checks do not qualify model loading, real audio or signed releases.

One Rust process lease spans startup, capture and owned stop. New starts wait for
prior source cleanup. Parent EOF interrupts blocked feed/drain, revokes authority,
cancels/reaps ASR/correction/MT, removes its owned private spool and ends the history writer.
Explicit stop drains within existing budgets, ends SQLite at observed stop and
reports cleanup before exit. A nonreading parent cannot block the reactor with
stdout: a process-owned std writer thread has a bounded async confirmation; on
timeout, native cleanup runs before process exit. Python confirms host exit and
retains its lease if cleanup is unconfirmed/nonzero rather than oversubscribing
workers. Such a quarantined runtime requires server restart; no automatic retry
or backend switch is claimed. Blocking filesystem/kernel syscalls retain the
limits documented in the existing ingress/history modules.

Rust MT also retains the existing Python M2M100 native-child lease until confirmed
cleanup, so preparation/repair cannot overlap it. Cache clear and preparation
refuse pending source cleanup and quarantined ownership, including after the HTTP
stop waiter is cancelled. A new Python start cannot bypass an unconfirmed Rust
lease; rollback requires clean stop or a server restart.

The bridge captures the existing title/presenter, the ASR-consumed description
prefix (1000 characters) and reference excerpt (3000), explicit operator hotwords,
preset, and enabled glossary entries matching the actual source language and
preset. The full original API/session context is retained in existing Python
state/history; inference-prefix extraction does not rewrite it. Rust installs this
snapshot once under the authority lock before source admission. Source revisions
keep the same inputs, stop/restart clears them, and an MT context cannot replace
the captured preset/glossary. Oversized snapshots are rejected rather than dropping
relevant hints or interrupting an existing session.

Each immutable ASR descriptor carries that snapshot. The shipped Python worker
validates the existing `SessionContext` before PCM/model loading and calls the same
faster-whisper prompt and `asr_hotwords(source_language=...)` implementation as the
Python runtime. Explicit hotwords retain their existing operator-defined scope;
disabled, wrong-preset or wrong-source glossary hints do not apply. MT receives
only preset/glossary and uses the unchanged relevance and fail-closed term guard.
ASR still consumes only the first 1200 reference characters; optional correction
uses the existing 3000-character excerpt. Sharing the larger captured excerpt
does not widen the ASR prompt or mutate the archived full context.
Titles, presenters and references are ASR hints, not evidence of model accuracy.

## Source-first correction arbitration

The product Rust route enables the existing deterministic corrector even when the
optional correction provider is `none`, matching Python behavior. Rust publishes
raw STABLE first and then admits correction without blocking ASR or observer
publication. The Python correction worker evaluates only identity-bound immutable
leaves: the existing `DeterministicCorrector` and, when selected, the existing
`OllamaConstrainedCorrector` with its candidate safety validator. It neither owns
captions nor constructs provenance, commits, revisions or database writes.

Rust chooses the deterministic result, accepted LLM candidate or fallback and
publishes CORRECTED with the existing typed `CorrectionProvenance`. Raw
`original_text` and ASR sample-clock provenance remain unchanged. A failed/hung
LLM retains an already completed deterministic result. When the deterministic
worker itself cannot complete or its bounded queue is full, raw text survives as
an explicit fallback with no claimed deterministic change or LLM application.
The authority retains a completed deterministic result and whether the LLM leaf
was invoked, so cancellation/EOF fallback preserves that known phase. Queued jobs
that never reach the leaf do not claim an LLM attempt or an applied correction.
One active job can retain at most 20,000 deterministic characters in addition to
the encoded canonical cache; this is bounded metadata, not a model RSS limit.
The shared lexical safety checks are not complete semantic certification.

Each source retains the same MT jobs and absolute target deadlines captured before
correction queue admission. Correction wait/load/inference consumes that original
budget; handing the selected text to MT never renews it. Each target still fails
independently. Without MT, selected targets stay `unsupported` and the source
reaches COMMITTED only after correction terminates. Stages advance through
STABLE → CORRECTED → TRANSLATED → COMMITTED (TRANSLATED is omitted without MT).
Snapshots, reconnect and SQLite use those same canonical events and correction
metadata; the Python relay does not rerun correction or overwrite original text.

Nonterminal canonical records have a separate 512-frame / 16MiB encoded-byte
limit, including a transient 256-result ASR batch. This is a serialized-data
bound, not a process RSS limit. Completion removes those records. The live view
stays at 100 captions / 1MiB: rolled-off pending correction/MT still updates
history and per-ID versions, without reappearing in the view or allocating an
observer publication serial that could leave the relay waiting for a missing
frame. Stop/restart and parent EOF terminalize retained raw/known-corrected source
and fail only pending targets before revocation, preserving successful targets
without launching more inference. Cancellation may move STABLE directly to
COMMITTED with explicit raw fallback. Normal stop retains its relay ACK barrier;
unexpected EOF does not promise delivery to a disconnected observer.

Generation/revision checks and cancellation fence both correction publication and
MT handoff. Jobs hold weak authority and share immutable context through `Arc`,
avoiding an owner cycle or per-segment glossary copies before bounded admission.
Armed queue/owner fallback occurs outside the authority mutex. End/stop counts
pending correction even without MT, drains within configured budgets and confirms
the child's own reap signal. Replacing a shared slot cannot change that signal.

Optional Ollama preparation is bounded to three seconds and degrades to
deterministic correction when unavailable. Parent EOF during that preparation
cleans existing owners before an ASR child/spool can start. Endpoint/model options
travel only in private stdin frames, not command-line arguments, events or errors.
HTTP uses the existing bounded, no-proxy/no-redirect provider path. The separately
owned Ollama server may continue inference/cache models after its HTTP caller
disconnects; reaping the Python leaf does not prove external model unload.

SQLite lock/full/write/queue failures mark the archive incomplete without gating
source or MT publication. Rust history metrics propagate in control ACKs and final
stop state. Python does not overwrite Rust's observed `ended_at` on normal stop.

## Independent recording ownership

The existing opt-in `record_audio` setting and separate recording start/stop APIs
reuse the API-owned `SessionRecordingWriter`, private recording routes and durable
recording journal. Rust remains the sole caption/revision/history authority;
recording does not start the Python caption pipeline or its persistence queue.
This is source-mode media parity, not complete migration of media control to Rust.

Recording requires a running audio-input session with an available persisted
history owner. Failed source startup, lost native authority or unavailable Rust
history cannot open a new recording. A later history error disables new recording
admission; an already owned media file still receives normal independent cleanup.
Disk/open/queue failures stop only recording, leaving caption admission and
publication independent. Public audience events contain no audio file or path.

The received valid PCM sample clock supplies exact start offsets, including audio
before recording and gaps between recordings. The media sink receives validated
PCM before native admission, preserving the existing received-audio contract even
when native caption processing rejects a later frame. Recording stop does not end
the source. Cancelled HTTP controls retain their independent open/finalize owner;
a replacement source waits until those operations close their file descriptors.
Source stop, including cleanup after unexpected parent EOF, finalizes owned media
even when native stop reports failure. EOF disables recording availability; an
already open file remains owned until existing source/recording stop cleanup.
Existing WAV header close/flush/fsync/hash, journal reservation,
atomic promotion, session ownership checks and interrupted-promotion recovery are
unchanged. Finalized timestamps retain the observed journal time, not restart time.
Blocking filesystem/kernel calls retain the existing writer's cancellation limits.

## Enable, verify and roll back

Build with the existing pinned toolchain and prepare approved local caches first:

```sh
cargo +1.90.0 build --locked --release --manifest-path crates/Cargo.toml --bin langtextflow-runtime
```

Set `LANGTEXTFLOW_RUNTIME_CORE=rust` and
`LANGTEXTFLOW_RUST_RUNTIME_BINARY=/absolute/path/to/crates/target/release/langtextflow-runtime`
(`.exe` on Windows) before starting the normal source-mode server. Use the stated
supported settings in the existing operator UI. Do not use this executable as an
arbitrary uploaded/untrusted helper. No first-use provisioning is performed.

After an owned clean stop, select `LANGTEXTFLOW_RUNTIME_CORE=python` and restart
the server to roll back. The default Python path and schema remain available.
The automated test also switches the same runtime to Python and exercises the
unchanged start/stop and Python persistence path after two clean Rust sessions.

`cargo test --locked --manifest-path crates/Cargo.toml --workspace` builds the
shipped binary and invokes existing-API integration on Linux/Windows/macOS. The
Python contract interpreter is the repository dev venv, or `LTF_HISTORY_PYTHON`.
It uses real Rust/Python OS processes, real internal TCP WebSockets, existing ASGI
HTTP/audio/audience routes and actual SQLite, with **synthetic immutable inference
replies**. Tests cover source-first partial target failure, reconnect/history/SRT,
restart, Python rollback, pre-mutation refusal, startup/stop cancellation, EOF,
oversized declared frames, nonreading control output, private-spool/reap and
write-failure/later-stop behavior. These tests perform no model download or native
model inference, and do not certify microphone hardware or real model quality.
Context tests additionally exercise actual API→Rust→ASR/MT child transport for all
presets and KO/EN/JA source scope, immutable capture, oversized pre-transition
refusal, revision/restart fencing, and the shipped Python workers with synthetic
external native libraries for prompt/hotword and all-six-direction glossary guards.
Correction regressions additionally exercise actual leaf processes and fake HTTP,
raw-first observation, chosen-text MT handoff, unchanged original/SQLite metadata,
queue overflow, deadline consumption, revision fencing, hang/failure fallback,
reconnect, parent EOF during HTTP/prepare and per-child reap with shared-slot reuse.
Recording regressions use the same real Rust API path with synthetic inference,
actual PCM/WAV bytes, hashes and SQLite: automatic and late start, stop/resume
offsets, private asset ownership, failed startup/history admission, disk/queue
failure isolation, cancelled HTTP controls, EOF finalization and journal recovery.
They complement the existing independent writer/route/security tests and do not
certify physical microphone capture or frozen application packaging.

## Required remaining work

AUTO/failover parity, complete Rust queue/latency
telemetry in the existing metrics API, desktop/frozen binary bundling and lifecycle,
native app capture/recording validation and
reviewed gradual default cutover/rollback remain **required**. This supported
subset is an intermediate delivery, not a reduction of the approved target.
Real six-direction/two-target semantics, aligned timing, long native soak/latency,
rights, signing and physical 4/8GB qualification remain separate open gates.
