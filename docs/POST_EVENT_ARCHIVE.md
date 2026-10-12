# Post-event archive (initial implementation)

> **Status:** pre-field, draft feature. Source transcript remains the source of truth. The AI output is an **unreviewed draft** and must be checked before publication.

## Model

Existing `sessions` and `transcript_segments` are unchanged. New tables:

- `session_recordings(recording_id, session_id, filename, media_type, size_bytes, sha256, duration_seconds, sample_rate, channels, created_at)`
- `session_summaries(summary_id, session_id, language, provider, model, transcript_sha256, prompt_version, status, content, error, created_at, updated_at)`

Foreign keys cascade with session deletion. WAV bytes live in `<database-directory>/recordings/` and **never** as a SQLite BLOB. Managed storage uses server-generated filenames; original user filenames are not trusted. A deleted session removes its recording metadata and attempts managed file cleanup. Old orphan files following abrupt process termination may require a maintenance job (tracked in #79).

## User path

1. Finish the session, then open **History > Details > Event archive**.
2. Import a recording made through a permitted recording mechanism. Current initial UI imports **PCM16 WAV** (mono/stereo, 8–48 kHz, up to 512 MiB and four hours). **Live microphone recording is not yet implemented**; #79 covers an opt-in capture writer without blocking the ASR queue.
3. Play imported recordings from the same session panel.
4. Choose **Generate / retry with local Ollama** to create a post-event summary. It defaults to the configured local `ollama_correction_model` (currently `qwen3.5:4b`); that model must be installed and reachable. No external provider is silently selected.
5. The summary runs in an async job. The panel polls status; failure never changes or removes the original transcript. Generation is idempotent when the same transcript hash/provider/model/language/prompt version already has a completed result.

## Automatic recording (stacked implementation)

Live audio recording is available as an explicit **per-session opt-in** before starting a real ASR engine. The existing streaming `/ws/audio` float32 PCM is mirrored to a **bounded, asynchronous PCM16 WAV writer**; filesystem backpressure does not block live ASR. A complete recording is linked to `session_id` after stop and appears in the same Event archive panel. Recording defaults to off, and a demo/mock session cannot enable it.

Failure behavior: if the recording queue overflows, storage is exhausted, or WAV finalization fails, the recorder discards incomplete audio and sets `SessionState.recording_error`, while the caption pipeline continues. The operator must check the recording status before publishing. Interrupted `.partial` recordings are not automatically represented as complete assets; safe startup recovery/retention policies remain #79.

Recordings are local plaintext files with `0600` creation permissions (subject to platform filesystem semantics); **participant notice/consent is the operator's responsibility**. Browser controls do not persist recording opt-in across sessions. Imported WAV files remain supported.

## Operator-only API

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/history/{id}/archive` | List recording metadata and latest summary state |
| `POST /api/v1/history/{id}/recordings` | Stream raw `audio/wav` bytes of an authorized PCM16 recording, size-limited |
| `GET /api/v1/history/{id}/recordings/{recording_id}/audio` | Local playback / download |
| `POST /api/v1/history/{id}/summary` | Start nonblocking post-session summary generation |

Request body for summary (optional keys):

```json
{"language":"ko","provider":"ollama"}
```

Explicitly selecting the OpenAI-compatible provider requires `allow_external_provider:true` and a configured model. An OpenAI-compatible endpoint may be local or remote; the user must verify data handling terms before choosing an external endpoint.

**All routes** are protected by the existing loopback-only Operator API and browser Origin restrictions. Audience join codes do not grant access.

## Grounding and limitations

Only **committed original-language** transcript segments are evidence; translated captions, provisional text and user-supplied reference documents are not fed as speech evidence. The text includes segment IDs and source timestamps. Large transcripts are chunked and hierarchically reduced; the transcript SHA-256 distinguishes old summaries from new revisions. The system prompt treats transcripts as untrusted data. These measures reduce, but do **not eliminate**, hallucination and prompt-injection risk.

- Auto recording, time-accurate seek from the transcript, privacy notice/consent, retention quotas and encrypted-at-rest handling require #79.
- Summary editing, human approval, proof-link validation, model quality benchmarks and stale-UI banners require #80 and #81.
- Real device/microphone 30/60/90-minute acceptance remains separate.
- WAV imports are **not automatically aligned** with the live transcript; imported audio may include different leading/trailing silence.
- Incomplete/interrupted sessions can have missing committed text; verify missing portions before dissemination.
- Current worker is in-process. A restart marks an in-flight summary attempt as failed; distributed retry queues are not implemented.

## Tests

`uv run pytest -q apps/server/tests/test_post_event_archive.py`

This is a separate test from the prior field acceptance and new real-speech benchmark: #77.
