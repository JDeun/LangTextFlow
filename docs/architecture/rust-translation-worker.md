# Rust v1 local translation worker

Status: opt-in worker boundary toward the [approved Rust orchestration target](https://github.com/JDeun/LangTextFlow/issues/344#issuecomment-6536557339).
This does not replace the production Python caption runtime or implement Rust MT
scheduling, target-state projection, persistence or desktop cutover.

The existing `langtextflow.translation.m2m100_worker` accepts an explicit
`--rust-protocol` mode. Its default ready-ACK/private runtime protocol remains
compatible. Both modes use the same cached M2M100 CPU INT8 loader, tokenizer,
target prefix and bounded decoder. There is no nested inference subprocess,
network model bootstrap, new adapter or new dependency.

```bash
python -m langtextflow.translation.m2m100_worker --rust-protocol \
  /path/to/prepared/ct2 /path/to/prepared/tokenizer 2
```

The trusted local Rust parent supplies the existing v1 `WorkerRequest` fields:
`task_kind=translation`, distinct explicit KO/EN/JA source/target, current
session/generation/segment/revision and an admitted 1–120000ms deadline.
`payload` is a JSON string with exactly `text` and `context`; text is nonempty
and at most 20000 characters. Context accepts only `preset` and `glossary`,
validated by the existing bounded `SessionContext`/`GlossaryEntry` models.
Reference documents, endpoints and other unused context fields are rejected.

Shared identity/routing checks are also used by the ASR worker. Invalid requests
are rejected before native model loading. A valid first request loads only local
model/tokenizer files, with offline/no-Torch environment flags; subsequent
requests reuse the same native tuple. The worker emits no unsolicited ready ACK
in Rust mode. `WorkerReply.result` contains only the immutable translated string.
It neither creates nor commits an application caption, and does not assert that
configured weights have independently verified identity or semantic quality.

Translation applies the production source-language/preset/enabled glossary scope.
Missing required terms fail the target instead of rewriting the translation.
Target prefix, empty/source-copy/JSON-like output and the existing 20000-character
translation bound are checked by the shared decoder. A completed output rejected
for these text checks or a missing required glossary term raises the existing
`TranslationError` subtype `TranslationRejected`. It returns the request identity
with `status=error`, `result=null`, `error=translation_rejected`, then keeps the
model for the peer target and later captions. This is still a failed target:
numeric source copies such as `2010...` are never published as translations.
The legacy protocol uses exactly `{id,error}` for this same fixed rejection code;
successful legacy responses remain exactly `{id,text}`.

Native/model/cache errors and invalid target prefixes return the request identity
with `translation_worker_failed`, then exit. Invalid framing/identity, unknown
errors and extra response fields remain terminal at the parent. Neither protocol
exposes paths, rejected output, source terms, provider details or tracebacks in
the rejection frame. Cancellation/timeouts still require owned kill/reap before
replacement; a rejection never retries the failed target automatically.

Both v1 JSONL frames are capped at 128KiB including newline. The native owner uses
one pending request slot in Rust mode; overflow/invalid framing terminates it.
The raw-FD reader detects parent EOF even during a blocked native load/inference,
and exits without waiting for native finalizers. The parent must still enforce
its own monotonic deadline, cancellation, child admission and verified reap before
restart using the existing Rust supervisor. The worker does not treat its
`deadline_ms` metadata as permission to commit or as a local hard timer.

Regression tests exercise the shipped worker and shared native adapter with only
external native libraries replaced by synthetic fixtures. They cover all six
directions, identity, one-time load/reuse after output rejection, scoped glossary, native failure,
oversized/invalid ingress, parent EOF during blocked work and the existing Python
ready ACK. These are protocol/lifecycle tests, not six-direction model scores.
Actual cached-model execution and later Rust end-to-end measurements must retain
their own source/hash/environment receipts. Model/hardware/field acceptance and
production ownership handoff remain open.
