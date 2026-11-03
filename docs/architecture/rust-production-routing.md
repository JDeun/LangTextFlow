# Rust routing through the existing application API

Status: intermediate opt-in source-mode application wiring toward the **required**
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
executable without a shell. Python supplies the existing session metadata and
initializes/migrates the existing SQLite schema. Rust owns the accepted session
generation, continuous PCM/VAD, ASR worker, bounded independent-target MT,
canonical revision/commit projection, authorized observer and caption-history
writes. Inference workers receive immutable existing requests, not database paths.
Python's pipeline and caption persistence queue are bypassed. Its CaptionStore
and per-client hub are UI relay mirrors; they neither transform source text nor
create new caption revisions. Rust event timestamps/provenance survive unchanged.

The supported subset is cached CPU/int8 faster-whisper in four-second windows,
KO/EN/JA source with one or two distinct targets, no correction, no recording,
general context without glossary/hotwords/references/description, and none or
cached M2M100 CT2 MT. Display title/presenter/audience settings remain available.
Unsupported combinations, frozen mode, incompatible chunk/VAD/device settings
and a missing executable are refused **before database initialization or live
session mutation**. There is no silent fallback or omitted requested feature.
The fixed 100-segment live projection is also checked against server configuration.
Starting recording separately is also refused and recording availability is false.
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
| Audio/control | One-byte tag + little-endian u32 length; audio ≤1MiB and float32-aligned; end/stop have zero payload |
| Control queue | One pending command; excess/malformed input closes authority admission before payload allocation |
| Control output | ≤1KiB JSON replies; one queued writer operation; one-second async write confirmation |
| Parent ACK | Session ID, generation and ordered sequence checked; ≤15s startup reply / ≤15s + configured target deadline exchange |
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

One Rust process lease spans startup, capture and owned stop. New starts wait for
prior source cleanup. Parent EOF interrupts blocked feed/drain, revokes authority,
cancels/reaps ASR/MT, removes its owned private spool and ends the history writer.
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

SQLite lock/full/write/queue failures mark the archive incomplete without gating
source or MT publication. Rust history metrics propagate in control ACKs and final
stop state. Python does not overwrite Rust's observed `ended_at` on normal stop.

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

## Required remaining work

Correction/context/hotword and AUTO/failover parity, independent recording parity,
desktop/frozen binary bundling and lifecycle, native app capture validation and
reviewed gradual default cutover/rollback remain **required**. This supported
subset is an intermediate delivery, not a reduction of the approved target.
Real six-direction/two-target semantics, aligned timing, long native soak/latency,
rights, signing and physical 4/8GB qualification remain separate open gates.
