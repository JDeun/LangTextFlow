# Rust source and independent-target translation prototype

This opt-in slice extends the [canonical caption/WebSocket prototype](rust-caption-websocket-prototype.md)
using the [shipped M2M100 inference mode](rust-translation-worker.md). Rust owns
source admission, target work admission, deadlines, versioned publication and the
WebSocket projection. Python owns only cached native inference. The approved
[Rust-core target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339)
still requires further integration; the production Python application is unchanged.

## Caption authority

`start_observed` retains its existing source-only behavior: selected targets are
`unsupported` and the source completes without MT. An explicit
`enable_translation` before the first admission selects the bounded MT lane.
The existing KO/EN/JA distinct-pair contract supports all six directions.

Rust immediately emits the accepted ASR source as STABLE with selected targets
`pending`. Source publication never awaits a translation, native worker or socket.
Each target result advances the same event's version and serial independently:

| State | Meaning |
|---|---|
| `pending` | Work has not reached a terminal result |
| `available` | Current fenced inference result passed the existing native decoder/glossary checks |
| `failed` | Admission, worker, identity, validation or owner failure |
| `timed_out` | The absolute target deadline expired, including queue wait |
| `unsupported` | Source-only mode explicitly selected no translator |

The stage becomes COMMITTED when every selected target is terminal. One failed
target never removes a successful peer or copies the source into a translation.
`text` and `original_text` stay identical and immutable within that source revision;
there is no correction stage. Original sample-clock provenance is preserved.
Translation provenance names the M2M100 provider, while configured/verified model
and revision remain null: local cache paths do not attest checkpoint identity.

Before any source segment in a chunk reaches COMMITTED, `revise` may advance its
source revision. This cancels/fences all old target jobs and preserves its public
source slot. Rust removes that chunk's prior public IDs and publishes an
authoritative snapshot to current observers; unrelated committed sources remain.
This also removes obsolete segments when a revision shrinks or becomes empty.
Replacement source publication uses higher versions with the new revision.
Per-ID version history survives display eviction/invalidation, with a bounded
32,768-ID session limit matching 128 admitted chunks × 256 inferred segments.
Once any segment is COMMITTED, replacement of that chunk is rejected.
Session restart invalidates old generation jobs and credentials. Late source or
target results cannot populate the replacement session.

## Bounded native lane and ownership

`MtDispatcher` owns one persistent `SupervisedWorker` and a FIFO of 1–16 target
jobs (the example uses four). Its process admission slot is separate from ASR.
The caller must retain and reuse the **same** shared MT slot when replacing a
dispatcher, so an unreaped old child cannot be bypassed with a new owner.

Each target's 1–120,000ms absolute deadline is captured before queue admission;
loading, queue wait, control writes and inference all consume it. Queue admission
uses `try_reserve` without awaiting; overflow is an explicit failed target. Source
and glossary JSON are copied only after capacity is reserved. The existing v1
128KiB frame limit still applies. Context carries only `preset` and `glossary`;
the shipped worker validates their existing product schema before lazy loading.
Context size is counted through a private limited writer, rejecting over 64KiB
without first allocating a full serialized output buffer. A final publication
check downgrades success if serialization consumed the remaining target budget.

The single native child serializes targets. A slow target can consume a queued
peer's deadline; that peer then times out explicitly. This is bounded isolation
from ASR and observer transport, not parallel per-language model execution.
Successful requests reuse the native model. A timeout, cancellation, malformed
reply or error makes that child terminal; a later target can start a replacement
only after the shared slot's confirmed reap.

Jobs hold a weak reference to caption authority. The dispatcher task does not own
an `AsrSession`; there is no session→sender→task→session cycle. An armed job dropped
by queue rejection, task cancellation or owner shutdown publishes terminal failure
only if its session/revision remains current. Session stop/restart removes old
pending work and requests active cancellation. The dispatcher is its own explicit
lifetime owner: stop it to unload an idle model as well.

`stop` closes queued work, cancels the active owned exchange, and waits within a
bounded budget for its child slot to be released. Unconfirmed reap is an error.
Dropping an owner also drops the owned exchange; Supervisor kills and independently
reaps it, retaining its slot until actual exit even if Tokio has already ended.
No worker stderr, filesystem path or exception body enters public caption errors.

## Existing cached-model example

The seven-argument source invocation and ten-argument source/WS invocation remain
supported. Append four arguments to the WS form to opt into actual cached MT:

```bash
export PYTHONPATH=apps/server
cargo run --locked --manifest-path crates/Cargo.toml --example asr_worker_slice -- \
  /existing/asr-python /approved/public.wav /prepared/whisper /existing/cache 2 ko 199680 \
  0 http://127.0.0.1:5185 /private/new-observer.json \
  /existing/mt-python /prepared/m2m100-ct2 /prepared/m2m100-tokenizer 2
```

This example uses CPU INT8, two independent configured thread counts, general
preset, glossary off, beam 1 and maximum 256 decoding tokens. It drains current
target work within a bounded budget, then stops both inference owners and its
listener. The observer credential file remains caller-owned and private, as in
the previous slice. Shutdown is not a transport-delivery or persistence guarantee.
No model is downloaded and no cloud fallback is available.

Actual OS-child/WebSocket regressions cover mixed target success/failure with
reconnect, slow MT while another source publishes, hung inference and fresh-child
retry, saturated queues with queue time charged to the deadline, wrong reply
identity, revision/session replacement and owner stop/drop with confirmed slot
release. Two-to-one/empty revisions and display eviction preserve current-view
membership and monotonic versions. These synthetic tests establish ownership and wire behavior, not model
quality or native word alignment. Cached native receipts are separate from these
synthetic regressions and preserve unknown checkpoint provenance.

Remaining acceptance includes continuous audio/VAD, correction scheduling,
durable persistence/history, production operator/audience/desktop routing,
rollback parity, and model/device/field validation. No production cutover or
completion of the full approved architecture is claimed.
