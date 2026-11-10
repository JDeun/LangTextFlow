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

The supported subset includes AUTO, explicit VibeVoice, the deterministic mock source,
and cached faster-whisper with bounded device/compute/window settings,
KO/EN/JA source with one or two distinct targets, deterministic correction with
optional explicitly selected Ollama correction and independent audio recording,
any existing preset with source-scoped glossary/hotwords and ASR context hints,
and none or cached M2M100 CT2 MT. Display/audience settings remain available.
Unsupported combinations, invalid chunk/VAD/device settings
and a missing executable are refused **before database initialization or live
session mutation**. There is no silent fallback or omitted requested feature.
The live projection uses the configured count ceiling and its existing byte ceiling.
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
| Audio/control | One-byte tag + little-endian u32 length; audio ≤configured 4B–8MiB, float32-aligned; declared length checked before allocation; end/stop have zero payload |
| Control queue | One pending command; excess/malformed input closes authority admission before payload allocation |
| Control output | ≤1KiB ready/ACK/stopped lines; one queued writer operation; one-second async write confirmation |
| Native metrics | ≤4KiB including newline; 250ms coalesced watch value; one typed final pipe frame before stopped, without enlarging ACKs |
| Parent ACK | Session ID, generation and ordered sequence checked; startup retains the configured HTTP start deadline (up to 600s); regular exchange ≤15s + max(configured correction, target budget) |
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
The [Mac ARM64 receipt](../knowledge/raw/2026-11-10-frozen-rust-idle-wiring.json)
records passing worker controls and two owned start/stop/restart cycles at source
`700600a`, alongside the earlier failed PyInstaller 6.22.2 attempt. Windows and
actual cached-model execution remain separate acceptance checks.
The later [native frozen control](../knowledge/raw/2026-11-10-frozen-rust-native-control.json)
at backend source `15c8e29` used one public KO clip and fixed cached base/M2M
files: three committed sources, six available targets, matching live/history
captions and recorded PCM, observed worker cleanup and graceful backend exit.
This is one backend wiring control with earlier frontend assets. It does not
qualify later settings/provider revisions, packaged UI, model quality or release.

One Rust process lease spans startup, capture and owned stop. New starts wait for
prior source cleanup. Parent EOF interrupts blocked feed/drain, revokes authority,
cancels/reaps ASR/correction/MT, removes its owned private spool and ends the history writer.
Explicit stop drains within existing budgets, ends SQLite at observed stop and
reports cleanup before exit. A nonreading parent cannot block the reactor with
stdout: a process-owned std writer thread has a bounded async confirmation; on
timeout, native cleanup runs before process exit. Python confirms host exit and
retains its lease if cleanup is unconfirmed/nonzero rather than oversubscribing
workers. Such a quarantined runtime requires server restart; no automatic retry
or backend switch bypasses that ownership. A model/start/source refusal whose
ASR, correction, MT, spool, history and observer cleanup is confirmed reports an
error but exits zero to certify cleanup, allowing an explicit new session.
Zero exit does not certify successful preparation or inference. Protocol/output
failures retain conservative nonzero/quarantine behavior. Blocking filesystem/kernel syscalls retain the
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

## Deterministic mock source

Rust owns the existing demo timer, without a Python caption pipeline, ASR worker,
PCM spool or model lookup. It retains the three Korean sample strings, selected
source-language label, `demo-{cycle:03}-{index:02}` IDs, immediate partial text
with the last three characters removed, 0.55-second partial→stable delay and
2.4-second pause before the next sample. Confidence remains 0.72/0.93; synthetic
timestamps remain six seconds apart with a five-second stable duration.
Stable sources use the same bounded correction/independent-target MT, projection,
observer and SQLite history paths as real ASR. Partial events have null original
text/end time, no target work, and remain partial if stopped before stability.
Stop/restart owns and cancels the timer; retired generations cannot publish.
The existing public-ID bound still applies and its exhaustion fails explicitly.

The bridge advertises no audio input or recording availability. Ending microphone
input is a no-op; it does not stop the demo timer. Provenance explicitly says
`provider=mock`, `timestamp_basis=synthetic`, with no sample, model or AUTO/replay
claims. Native metrics use `ingress=null`; the API mirror, UI and collector keep
PCM queue values unmeasured. Demo output is not native inference or model quality
evidence, even when optional translation/correction workers are configured.

## Immutable audio and Whisper configuration

The trusted bootstrap installs strict version-1 `audio_options` once: Whisper
`chunk_samples` (1–1,920,000 at 16kHz), finite VAD threshold, hangover (0–10,000
original frames), maximum input frame bytes (4–8MiB, a multiple of four), and
live segment count (1–10,000). Missing options preserve the prototype defaults:
four seconds, −45dBFS, three frames, 1MiB and 100 segments. Python rejects invalid
configuration before replacing a session or initializing history. Fractional
chunk settings preserve the existing float-to-byte truncation/minimum policy;
a window that would contain an incomplete float32 sample is rejected.

The existing effective-low rule is server-low OR request-low: low Whisper uses
CPU/int8 and configured threads; standard uses configured device/compute and
library-default threads. Optional strict `whisper_options` is sent only when
Whisper can be selected. It travels in audio-free `prepare.inference_options` and
must be echoed exactly in its successful ACK. The leaf binds the options before
model loading and refuses changing them in the same child. The three-argument
CLI and option-free prototype prepare/WAV calls retain their old contract.
Accepted `cuda` or compute names describe requested configuration; they do not
prove GPU availability, actual loaded compute precision or performance.

Rust still validates the entire original frame before one queue reservation,
copy or sample-clock change. Cancelled reservations admit no prefix. Large
VibeVoice frames are subdivided inside that owner into at-most-1MiB immutable
leaf descriptors, preserving one input admission/ACK. VibeVoice retains its
negotiated sample rate and chunk duration; Whisper window settings do not change
its clock. The 128KiB JSONL and 1MiB caption/snapshot bounds stay unchanged.
RMS/hangover remains monitoring only, with no PCM dropping. Live count and byte
bounds are independent ceilings; byte-driven evictions remain observable and
never delete original SQLite history. A Whisper window's absolute deadline is
its first accepted frame time plus the actual sample-clock duration plus the
existing 120-second native budget. Queue age consumes that deadline; a partial
tail adds only its actual length. Admission additionally caps native work at
now + 120 seconds, and the existing supervised exchange cap remains unchanged.
The window deadline is at most 240 seconds after its first frame. Required
outstanding work includes queue/parallelism policies and drain budgets; settings
and fast fake-input checks do not prove a realtime two-minute window can finish,
or that a GPU/physical hardware target qualifies.

## AUTO selection and continuous source ownership

Explicit `vibevoice` uses the same bounded streaming leaf and source-clock owner
with one candidate. It neither looks up a Whisper cache nor configures, starts
or falls back to Whisper, including in the low-resource profile. Startup or live
failure remains an explicit source failure. Its sample-rate and sequence-estimated
timestamps retain the real sidecar boundaries; AUTO generation/replay fields stay
null because no AUTO policy ran.

Rust owns the existing standard AUTO policy: VibeVoice first, then the locally
cached Whisper candidate after startup or live failure. A low-resource request
or server profile constructs only the Whisper candidate; it sends no sidecar
configuration, HTTP probe, WebSocket or audio. Standard AUTO can start VibeVoice
without a prepared fallback cache, but cannot download or invent a fallback.
The bootstrap `whisper_ready` flag means local cache eligibility, not a loaded
or verified checkpoint. Both AUTO Whisper and explicitly selected Whisper must
complete the new strict, audio-free `prepare` acknowledgement before readiness.
Only strict audio-free prepare/stream-open requests may use the configured
600-second startup budget; audio inference, append, poll and finish retain the
existing 120-second envelope limit. The parent owns the absolute deadline and
EOF cancellation throughout native or network awaits.
The unchanged three-argument Whisper CLI additionally accepts `auto` for the
standard AUTO and explicit-Whisper library-default thread policy; an effective
low request or server profile retains its configured thread count and one native
worker. This restores the existing Python policy without changing the CLI ABI.

The sidecar's actual 8–192kHz rate is returned to existing browser capture and
recording paths. Whisper remains 16kHz; a mismatch fails explicitly before a
Whisper child starts, without resampling retained PCM. One ingress owner bounds
the input queue to eight frames and honors smaller configured queues. Its byte
ring retains the configured replay tail (default eight seconds, maximum 120),
while its accepted integer sample cursor includes the entire original frame.
The existing total stream-duration bound still applies at the selected rate.
Fractional replay seconds retain the existing float-to-complete-PCM-sample
rounding policy rather than being rounded to whole milliseconds.

Python's new streaming leaf owns only a persistent HTTP/WebSocket transport and
returns immutable requested batches. Versioned open/append/poll/finish requests
retain worker request identity, candidate epoch and sample interval. Audio travels
through a Rust-owned private regular PCM file of at most 1MiB, never JSON/base64.
The leaf verifies regular-file identity, exact length, finite float samples and
continuous intervals. It uses the same immutable source snapshot and existing pure
VibeVoice context formatter; it does not construct a caption pipeline, provider
selector, event, generation, timestamp, SQLite row or replay policy. Text batches
have 128-message/96KiB UTF-8 limits and must fit the unchanged final 128KiB JSONL
envelope. Network redirects, ambient proxies and TLS-environment overrides are
disabled; failures expose fixed categories rather than endpoints or paths.

Rust retires an outgoing candidate epoch before awaiting its actual child reap.
At most two per-child reap proofs are retained; an available shared semaphore is
not proof that a particular predecessor exited. A replacement prepares and
replays bounded immutable configured-size Whisper WAV windows before activation. Its
provisional output is limited to 128 source segments and 1MiB of text; failure
discards it. A successful live replacement advances only the separate AUTO
generation once. Session generation, already accepted caption/correction/MT
work, original target deadlines and source-clock origin remain intact. Startup
fallback stays AUTO generation zero with no claimed live failover.

Replay rebases from integer origin/prefix samples. Fully covered text is dropped;
partial overlap uses the existing conservative exact suffix/prefix comparison
(equal counts of original characters, minimum four, maximum 240) and retains
original spelling. The pinned full-casefold implementation and its Unicode
version/licensing are recorded in the [dependency receipt](../knowledge/raw/2026-11-10-rust-auto-casefold-dependency.json).
The small regression corpus is also compared with the actual Python helper;
it is not a claim of equality across all Unicode database versions. There is no
normalization, fuzzy deduplication or fabricated native word alignment.

Canonical source provenance distinguishes sidecar sequence estimates from
Whisper sample-clock native hints/estimates and preserves AUTO generation,
timeline origin, replay prefix and overlap trimming. Verified model/revision
remains unknown. Rust's same bounded SQLite writer updates active provider
metadata without blocking source publication; Python mirrors status without
writing it again. Existing source metrics report provider/generation/failover
state. Transport append/poll and replay work are not ASR inference-duration or
live-caption-latency measurements. Processed samples count unique continuous
input intervals, while decoded-window counters include replay work.

End of input makes new recording admission unavailable while caption/history
ownership remains alive until the existing stop path finishes. Parent EOF also
interrupts network/model preparation and prevents a late fallback from starting.
The actual API tests exercise 48kHz primary/restart, low-profile no-probe,
startup refusal/fallback, live disconnect/replay, partial target failure,
sample-rate refusal, cancellation/reap, metadata and existing recording ownership
using fake HTTP/WebSocket/native inference. They do not establish native AUTO
quality, physical microphone behavior or frozen AUTO packaging.

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

### Rust measurements in the existing metrics API

`/api/v1/metrics` retains the current API and adds a typed `rust_authority` sample.
Its session/generation, increasing sequence, UTC sample time and local receipt age
identify the measurement. Readiness requires metrics protocol version 1, so a
stale helper binary fails explicitly. Missing, stale (over 1500ms), unavailable,
fresh and final samples remain distinct. A repeated sequence cannot renew
freshness; conflicting repeated data, another session/generation, unknown fields,
non-finite or out-of-range values fail closed. Final receipt age keeps advancing
after the engine is removed. `final` means the last measurement, not model quality;
`stop_error` separately identifies an unconfirmed/failed stop.

The Rust owner samples independently from native inference. One watch value per
authority coalesces metrics for the existing authenticated private observer;
metrics never enter the caption FIFO or allocate caption serials. A reconnect
receives the existing caption snapshot and latest metrics. The owned sampler is
stopped/joined before cleanup; after child/history/observer cleanup, exactly one
bounded final metrics frame precedes the stopped ACK. Unexpected parent EOF does
not guarantee a final public sample: the bridge exposes unavailable last-known
data rather than claiming an empty healthy queue. No transcript, context, model,
filesystem path, endpoint, token or arbitrary provider error enters the frame.

Queue statistics describe **jobs**, not semantic quality or speech segments.
Correction and target-translation lanes each retain at most 512 dequeued-job wait
values, with independent nearest-rank p50/p95 and sample counts. No observations means null.
`queued` counts admitted, not-yet-started jobs (including a dequeue handoff);
`capacity` is the configured FIFO and one additional execution slot bounds each
lane. The legacy postprocess depth/capacity therefore represents active plus
queued jobs. Unadmitted overflow/unavailable counts remain separate; admitted jobs
end once as succeeded, failed, expired or cancelled. These are validated leaf
execution outcomes; generation/revision and final publication guards still decide
whether a result can update a caption.
An MT queue wait starts at correction handoff; it is not total source-to-target
latency. The original target deadline still includes the correction wait, and
the source's separately reported pending-target count includes deferred jobs.

PCM and history queue values map to their actual Rust owners. Legacy combined
postprocess peaks/percentiles and segment counters remain null because independent
job populations cannot truthfully supply them. Python relay-client and browser
receipt counters retain their own boundary; the private Rust observer has separate
statistics. UI unknown values display a dash, and benchmark collection leaves an
unobserved peak null instead of inventing zero. Diagnostics serialize the same
typed data. Counters are bounded to JavaScript-safe integers; rings and snapshot
memory remain bounded. Snapshots read independent owners, not a transaction across
all counters; observed queue high-watermarks are not RSS or hardware qualification.

### Start and rollback

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

Remaining general-provider/ten-language target options and existing
queue/parallelism and deadline/drain configuration parity remain required before
default cutover. This settings wiring supplies no GPU/hardware qualification.
Desktop/frozen binary bundling and lifecycle,
native app capture/recording validation and
reviewed gradual default cutover/rollback remain **required**. This supported
subset is an intermediate delivery, not a reduction of the approved target.
Real six-direction/two-target semantics, aligned timing, long native soak/latency,
rights, signing and physical 4/8GB qualification remain separate open gates.
