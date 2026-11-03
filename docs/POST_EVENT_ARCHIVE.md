# Post-event archive (initial implementation)

> **Status:** pre-field. Source transcript remains the source of truth. AI output starts as an **unreviewed draft**; operators can edit, save and approve it. Approval locks that version and preserves the original AI content. This records operator review, not independent semantic certification.

## Model

Existing `sessions` and `transcript_segments` are unchanged. New tables:

- `session_recordings(recording_id, session_id, filename, media_type, size_bytes, sha256, duration_seconds, sample_rate, channels, created_at)`
- `session_summaries(summary_id, session_id, language, provider, model, transcript_sha256, prompt_version, status, content, error, created_at, updated_at)`
- `summary_reviews(review_id, summary_id, content, approved, created_at)` — append-only operator edits and approvals; cascades with the summary/session.
- `transcript_versions` retains each local retranscription attempt, immutable decoder segments,
  recording/audio SHA-256, model, language, decode parameters, status/progress and provenance.
  `transcript_reviews` retains append-only operator edits and approval snapshots;
  `approved_transcripts` selects the current approved version. All cascade with the session.
- Summary rows additionally retain `transcript_id` (null for live source) and the current
  `approved_transcript_id` at generation admission, so approval changes invalidate old summaries.

Foreign keys cascade with session deletion. WAV bytes live in `<database-directory>/recordings/` and **never** as a SQLite BLOB. Managed storage uses server-generated filenames; original user filenames are not trusted. A deleted session removes its recording metadata and attempts managed file cleanup. Old orphan files following abrupt process termination may require a maintenance job (tracked in #79).

## User path

1. Finish the session, then open **History > Details > Event archive**.
2. Use the per-session opt-in to record live microphone audio, or import an authorized **PCM16 WAV** (mono/stereo, 8–48 kHz, up to 512 MiB and four hours).
3. Play imported recordings from the same session panel.
4. Choose **Generate / retry with local Ollama** to create a post-event summary. It defaults to the configured local `ollama_correction_model` (currently `qwen3.5:4b`); that model must be installed and reachable. No external provider is silently selected.
5. The summary runs in an async job. The panel polls status; failure never changes or removes the original transcript. Generation is idempotent when the same transcript hash/provider/model/language/prompt version already has a completed result.
6. Compare the summary against the transcript, retain exact source citations, save a review draft, then explicitly approve and lock it. Concurrent edits are rejected with HTTP 409. A changed source hash blocks approval; generate a new summary from the new evidence. Text export uses the saved review when present; the original AI draft remains available in the panel. Project manifests/reports retain approval and stale status.
7. Use the source citation buttons to clear transcript filtering, select the original language and focus the referenced committed segment. Only exact IDs/timecodes in the loaded session can be linked. These controls navigate the transcript; they do not claim sample-accurate audio alignment.
8. Under **Post-event transcript**, select a recording, cached model and speech language to create a separate local draft. Inspect the original decoder output/provenance, edit text and explicitly approve it. Approval locks that version and marks earlier summaries stale. The summary source still defaults to live ASR; select the approved post-event version explicitly to summarize it. Source-reference navigation opens that summary's exact transcript version. Each version exports its original draft and complete review history as JSON.

Offline segments also offer **Seek source recording (estimated time)**. This uses that
version's own managed recording and decoder start time, waits for media metadata,
positions and focuses the player without starting playback. Live captions are not
automatically aligned to imported WAVs; this control does not claim word alignment.

## Automatic recording (stacked implementation)

Live audio recording is available as an explicit **per-session opt-in** before starting a real ASR engine. The existing streaming `/ws/audio` float32 PCM is mirrored to a **bounded, asynchronous PCM16 WAV writer**; filesystem backpressure does not block live ASR. A complete recording is linked to `session_id` after stop and appears in the same Event archive panel. Recording defaults to off, and a demo/mock session cannot enable it.

Failure behavior: if the recording queue overflows, storage is exhausted, or WAV finalization fails, the recorder discards incomplete audio and sets `SessionState.recording_error`, while the caption pipeline continues. The operator must check the recording status before publishing. Interrupted `.partial` recordings are not automatically represented as complete assets; safe startup recovery/retention policies remain #79.

Recordings are local plaintext files with `0600` creation permissions (subject to platform filesystem semantics); **participant notice/consent is the operator's responsibility**. Browser controls do not persist recording opt-in across sessions. Imported WAV files remain supported.


## Optional live recording (stacked PR #92)

- Recording remains **off by default** and must be opted into for each new
  microphone-enabled session, after informing participants and obtaining
  required consent. The choice resets when a session ends or aborts.
- A bounded in-memory queue copies PCM frames from the operator-only audio
  stream to a separate asynchronous WAV writer. Files are private,
  single-channel PCM16 and linked to the immutable `session_id`.
- A disk initialization error does not stop live captioning; the operator
  sees a distinct recording failure status. Disk flush errors or audio queue
  overflow invalidate the recording and prevent incomplete files being
  presented as successful.
- The recording is written first as a private `*.wav.partial` and only
  promoted to `*.wav` after finalization and disk flush. Its SHA-256,
  duration, codec and size are then registered in SQLite.
- Recording failures that occur mid-session are surfaced through a scoped
  operator-state poll; no new audience API or permission is introduced.
- A power loss may leave an **unregistered partial file**. On subsequent
  startup, only server-generated UUID-named partials older than 24 hours are
  reclaimed. Completed WAVs and newer/unknown files are preserved. Partial
  files are never advertised as complete; finalized-but-unindexed WAV recovery
  and broader retention governance remain tracked in #79.
- Session timestamps and audio frames currently lack independently verified
  sample-accurate seek alignment. Do not imply that clicking a caption seeks
  to that exact recorded word until alignment verification is implemented.

## Operator-only API

Summary prompt/source format `post-event-v3-source-context` keeps each original
`[segment-id at Ns]` marker followed by a JSON metadata object and spoken text.
The object contains `speaker` only when the transcript explicitly supplies it;
`{}` means no speaker information. Speaker strings and spoken text remain
untrusted data, with line separators normalized so they cannot insert new source
records. Speaker identities and diarization are never inferred.

Original transcript requests stay within 240,000 characters, 6,000 characters
per chunk, and 48 source chunks. A split segment retains the same original marker
and metadata on every fragment. Each next chunk carries up to 400 characters of
the preceding source text as context. Source overlap must not be counted as a
second utterance. Model-generated notes are reduced separately without overlap;
they remain intermediate notes rather than new transcript segments. Final
citations still undergo the existing exact-ID/time validation, which does not
prove factual entailment or correct speaker attribution.

The uniform metadata format changes the hashed source representation, including
transcripts without speaker metadata. Previously generated summaries can therefore
become stale after this upgrade even when their spoken text has not changed.
Existing original drafts, review history and approved text are never rewritten
or unlocked. Review the current evidence and explicitly generate a new summary
version before sharing an up-to-date result.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/history/{id}/archive` | List recording metadata and latest summary state |
| `POST /api/v1/history/{id}/recordings` | Stream raw `audio/wav` bytes of an authorized PCM16 recording, size-limited |
| `GET /api/v1/history/{id}/recordings/{recording_id}/audio` | Local playback; `?download=true` returns an attachment through the same operator boundary |
| `POST /api/v1/history/{id}/summary` | Start nonblocking post-session summary generation |
| `POST /api/v1/history/{id}/summaries/{summary_id}/cancel` | Cancel this instance's running summary job; completed drafts and approvals stay intact. Retry explicitly to create a new attempt. |
| `POST /api/v1/history/{id}/summaries/{summary_id}/reviews` | Save a cited review or approve and lock a completed summary; requires `content`, optional `approved`, and the latest `expected_review_id` (null for the first edit) |

Request body for summary (optional keys):

```json
{"language":"ko","provider":"ollama"}
```

Explicitly selecting the OpenAI-compatible provider requires `allow_external_provider:true` and a configured model. An OpenAI-compatible endpoint may be local or remote; the user must verify data handling terms before choosing an external endpoint.

**All routes** are protected by the existing loopback-only Operator API and browser Origin restrictions. Audience join codes do not grant access.

## Optional local second ASR pass (#104)

This pass requires a finished session and an already managed, consented WAV recording. It
never replaces live captions, edits the WAV, uploads speech, or automatically starts after
recording. Prepare the optional faster-whisper runtime and desired tiny/base/small model
through the existing model setup first. Jobs only use cached weights from the same
`model_cache_dir/huggingface` cache; they never download a model during transcription.

| Endpoint (under `/api/v1/history/{id}`) | Purpose |
|---|---|
| `GET /transcripts` | Up to 100 version metadata records, newest first, and current approved ID |
| `POST /transcripts` | Explicit local CPU job: `recording_id`, `model` (tiny/base/small), `language` (ko/en/ja) |
| `GET /transcripts/{transcript_id}` | Original decoder segments/provenance and latest operator review |
| `POST /transcripts/{transcript_id}/cancel` | Kill and reap decoder process before releasing its CPU slot |
| `POST /transcripts/{transcript_id}/reviews` | Append review: `segments`, `approved`, `expected_review_id` |
| `GET /transcripts/{transcript_id}/export` | JSON download of original draft, provenance and every saved review |

Example job request:

```json
{"recording_id":"managed-recording-id","model":"small","language":"ko"}
```

Example first operator review; later saves must send the last returned `review_id` as
`expected_review_id`:

```json
{"segments":[{"segment_id":"offline-000001","start_ms":0,"end_ms":1000,"text":"검토한 원문"}],"approved":true,"expected_review_id":null}
```

Segment IDs must be unique and segments ordered by time/ID, with integer timestamps inside
the recording. Limits are 20,000 segments, 6,000 characters per segment and 240,000 evidence
characters including marker allowance. Each session admits at most 100 transcript attempts;
each version retains up to 100 reviews. Approval locks that version. Approving a *new*
version explicitly changes the session's selected approved version while preserving all
earlier versions and reviews. A retry always creates a new version; it never overwrites a
failed attempt or reuses an old approval. Session JSON/live subtitle exports retain their
existing live-source meaning; use the dedicated transcript-version JSON export for drafts,
operator text and model provenance.

Exactly one local offline decoder process is admitted per application. It uses CPU INT8,
two inference threads, one worker, beam size five, independent 30-second PCM windows,
no VAD and no previous-window text conditioning. PCM is streamed window by window rather
than loading four hours into RAM. The model allowlist bounds model size; generated evidence
and persisted versions are capped. A storage reserve check precedes admission. This is
bounded application work, not an OS-enforced RSS guarantee or a distributed worker queue.
The overall deadline is two hours. Progress persists after every decoded window, capped
below 100% until a valid complete result is saved. Cancellation/deadline/shutdown kills and
waits for the process; a parent-pipe watchdog also stops orphan inference after a crash.
Startup marks interrupted jobs failed; the operator can explicitly retry from the unchanged
WAV. Interrupted partial decoder output is not advertised as a complete transcript.

Provenance includes audio SHA-256, weight-file SHA-256, faster-whisper/CTranslate2 versions,
decode parameters, language, run timestamps, processing duration, real-time factor and
empty-window/low-confidence counters. These counters are diagnostics, **not CER/WER**.
Audio hashes are checked before and after decoding. Only server-managed recording IDs are
accepted, and symlinks or recording hash mismatches fail closed.
Nonfinite, negative, inverted or distant decoder timestamps fail the attempt instead of
silently retiming evidence. An end timestamp up to 0.5 seconds beyond its own PCM window
may be clipped as decoder padding; `quality_counters.clipped_segments` records each case.

Summary generation retains the live original source when `transcript_id` is null/omitted.
To use a reviewed offline version, explicitly send its current approved ID:

```json
{"language":"ko","provider":"ollama","transcript_id":"approved-transcript-id"}
```

Its evidence digest includes transcript/review identity as well as cited text. A changed
approved selection makes previous summaries stale in session archives and project reports,
including summaries with coincidentally identical text. Regeneration remains explicit;
no previous AI draft or approved summary is silently rewritten. Citation clicks must use
the selected summary's version and review segments, rather than same-named live segments.

Deterministic tests cover lifecycle, actual process reaping, orphan shutdown, restart/retry,
review locking, stale summaries, LAN/origin rejection, managed paths and 30/60/90-minute
synthetic PCM window bounds. They use a fake decoder and do not establish native-model
memory, field accuracy or device performance. Same-audio FLEURS 4s/30s and tiny/base/small
comparison, independent CER/WER, actual native/model long-event soak and microphone quality
remain acceptance gates under #103/#104; no model/default is promoted by these tests.

## Grounding and limitations

Only **committed original-language** transcript segments are evidence; translated captions, provisional text and user-supplied reference documents are not fed as speech evidence. The text includes segment IDs and source timestamps. Large transcripts are chunked and hierarchically reduced; the transcript SHA-256 distinguishes old summaries from new revisions. The system prompt treats transcripts as untrusted data. These measures reduce, but do **not eliminate**, hallucination and prompt-injection risk.

New postprocessed events preserve `original_text` separately from corrected display `text`, including SQLite history and JSON exports. Summary evidence and project source excerpts prefer the saved ASR text. Existing rows migrate with a null original field: their pre-correction output cannot be recovered, so their stored display text remains the legacy evidence fallback. This migration does not invent or backfill missing ASR output. A changed source revision replaces raw evidence together with invalidating the older targets.

- Time-accurate seek, participant privacy notice/consent, retention quotas and encrypted-at-rest handling remain separate acceptance work under #79.
- Summary editing, approval locking, citation identity validation and stale-UI banners are implemented. Citation validity does not establish factual accuracy; model quality and independent human acceptance remain under #80 and #81.
- Real device/microphone 30/60/90-minute acceptance remains separate.
- WAV imports are **not automatically aligned** with the live transcript; imported audio may include different leading/trailing silence.
- Incomplete/interrupted sessions can have missing committed text; verify missing portions before dissemination.
- Summary work is in-process and limited to two active jobs across sessions. Additional admissions return 429 without creating a running record; duplicate unchanged completed requests remain idempotent. Each generation has a ten-minute overall deadline across hierarchical requests. Application shutdown cancels workers and marks unfinished attempts as failed; abrupt-process restart recovery remains available. Distributed retry queues are not implemented.

## Tests

`uv run pytest -q apps/server/tests/test_post_event_archive.py`

This is a separate test from the prior field acceptance and new real-speech benchmark: #77.
