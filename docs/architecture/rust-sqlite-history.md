# Rust canonical-event SQLite history boundary

Status: opt-in implemented persistence slice; production application routing is unchanged.
The required Rust-authoritative core target under issues #344/#154 still needs the
remaining audio/correction/routing/rollback integration. This slice does not approve
a model, physical device, release or field deployment.

## Existing database contract

The existing Python control plane calls `SessionRepository.initialize()` and
`create_session()` before attaching the Rust writer. Those methods remain the
schema/migration and session-metadata bootstrap authority in this slice. Python
inference workers receive no database handle, database path or storage capability.

`HistoryWriter::open` opens that existing database read/write, without creating an
absent file or parallel schema. It requires the migrated transcript columns and an
existing open session. The bound session ID, generation, original `started_at`,
source language and ordered targets must match the Rust observed session. An old
schema is rejected until the existing Python migration runs; historical unknown
original/provenance values stay unknown.

After `start_observed`, attach `writer.sink()` using `AsrSession::attach_history`
before the first source admission. Every canonical source and target revision is
enqueued from `CaptionProjection::apply`, independently of observer delivery and
live-view eviction. A session reset/stop closes the old sink before replacement.
Queued accepted old-session events drain into their original session; a late old
worker reply cannot enter the new projection or writer scope.

The closed SQL upsert mirrors `SessionRepository.upsert_segment`; a test compares
the actual Python method's SQL to the Rust literal. Existing version, adjacent
source-revision, stage and committed-original guards are preserved. All original
timestamps, independent target statuses, correction and ASR/MT provenance fields
are stored in the existing columns. There is no model-path hashing, inferred
checkpoint identity or historical provenance reconstruction. Event `emitted_at`
is retained; it is never replaced by disk flush time.

Source revisions also enqueue ordered invalidation batches for obsolete source
IDs (at most the existing 128 inferred segments per chunk), including IDs evicted
from the live view. Deletes require the bound session/start time, old revision,
maximum accepted version and an uncommitted row. Newer revisions and committed
originals cannot be removed. Two-to-one and two-to-empty revisions therefore do
not leave ghost rows in history. `invalidated` counts deleted rows; queue
accepted/persisted/stale/dropped counters count operations, including a bounded
invalidation batch as one operation.

## Bounded ownership and degraded operation

Each writer owns one OS thread and a caller-supplied semaphore permit. The permit
is retained through connection close/thread teardown, including cancelled startup,
drop and timeout. Use one shared slot for the runtime's writer admission; do not
create a replacement slot to bypass an unfinished owner.

The queue capacity is explicit, 1–32 canonical-frame/invalidation operations.
Existing projection bounds limit each frame to under 1 MiB. Source/MT/WebSocket publication only calls
`try_send`; it never waits for SQLite or a queue slot. Overflow increments dropped
and marks persistence incomplete. A SQLite failure rejects subsequent **history
enqueue only**, reports a fixed error category and drains/discards remaining
bounded work. ASR/PCM admission, MT and fast observer progress continue. Database
errors, paths, SQL text and credentials are not serialized into diagnostics.

`flush()` is a control-plane barrier with a three-second wait bound. `stop()` first
closes admission and captures the observed UTC end time, then drains and joins the
owned writer within a three-second wait budget. SQLite lock waits are bounded to
100 ms per operation. A stop timeout requests SQLite interruption and leaves the
owner/permit retained; the caller can await `stop()` again. Cancellation of a
borrowed stop future does not cancel the thread. Dropping the owner closes the
queue and requests interruption, marking the report incomplete. A kernel-level
filesystem stall cannot be forcibly killed as an OS thread; capacity stays held
until that thread actually exits.

An `Ok` stop means the thread joined; callers must also inspect `complete`,
`write_errors`, `dropped` and `stale`. `complete` tracks observed loss/errors,
not active-queue durability before a barrier. `stopped` becomes true after the
connection closes. If failure prevented writing the end timestamp, the session
remains open for the existing interrupted-session recovery contract. No successful
durability or device-latency claim follows from a degraded report.

## Verification and dependencies

`cargo +1.90.0 test --locked --manifest-path crates/Cargo.toml --workspace` runs
the existing protocols and the new actual SQLite tests. Install locked Python dev
dependencies with `uv sync --locked --extra dev` first; the parity fixture invokes
that virtual environment and the actual existing Python repository, HTTP history,
JSON/SRT export and private project backup/restore implementations. Inference is
synthetic, transported through real owned OS children and actual WebSockets. It is
not a model-inference quality or benchmark result.

Coverage includes one failed and one successful target, Unicode source/target
roundtrip, original/provenance/revision protection, old-session/reset fencing,
legacy migration, actual SQLite `SQLITE_FULL` from a page quota, trigger write
failure/redaction, queue overflow and locked database behavior. Actual child ASR,
MT and a fast WebSocket continue while persistence is degraded. Startup/stop
cancellation, retained capacity, bounded drain and admission reuse are tested.
The existing Rust protocol CI gate includes Linux, Windows and macOS native
compilation plus these Python reader contracts, with dev dependencies only.

The driver is exact `rusqlite=0.41.1`, defaults disabled, only `bundled` enabled:
SQLite 3.54.1 is compiled from the locked `libsqlite3-sys=0.39.1` sources. No ORM,
custom C FFI, external database service, SQLCipher, extension loading or WASM is
enabled. See the [verified dependency record](../knowledge/raw/2026-11-04-rust-sqlite-dependency.json)
and [notices](../../crates/langtextflow-core/THIRD_PARTY_NOTICES.md). The initial
audit found no vulnerability in the new dependencies and retained the separate
pre-existing Tokio `RUSTSEC-2026-0386` unsound warning for its own security fix;
that audit was not reported as warning-free.
