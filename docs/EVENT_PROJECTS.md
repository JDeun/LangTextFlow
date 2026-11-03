# Event Projects

Event Projects are the primary **organizing workspace**. An event may include a rehearsal, multiple speakers, interpretation sessions and Q&A. Each session keeps its own source/translated transcript and (when the separate post-event archive PR is merged) recording and summary; their durable IDs are not replaced with the project ID.

## Data model

```text
projects (project_id PK, title, description, event_date, location, status)
    │
    └── project_sessions (session_id PK/FK, project_id FK)
             │
             └── sessions (session_id PK)
                   └── transcript_segments (session_id FK)
                   └── future post-event recordings/summaries (session_id FK)
```

- One project has zero or more sessions; one session belongs to zero or one project (MVP).
- **Deleting a project only removes the association**; sessions, original captions, translations and future recording assets are preserved. To intentionally delete a session, use the existing session deletion action.
- Existing SQLite sessions migrate without needing a project and remain visible in **Unassigned sessions**.
- Linking a new session at creation time is performed in the same SQLite transaction as session creation; a non-existent project ID is rejected before a running session is stopped.
- Project dates are calendar dates (YYYY-MM-DD), not timestamps; session timestamps retain timezone-aware ISO 8601 values.

## Operator workflow

1. Open **History → Event projects** and create an event with title, optional date, location and description.
2. Select the project. Existing sessions can be assigned or moved using the Project selector next to each session row.
3. The session setup panel has a **Project** selector. Selecting an event associates new sessions automatically at creation.
4. Filter history by project or **Unassigned sessions**. You can continue exporting or reviewing individual sessions.
5. Editing project metadata does not change the source Session Context used by prior model runs.

No project CRUD/assignment endpoint is available to LAN audience clients; all APIs below inherit the existing loopback-only operator guard.

| Method | API | Meaning |
|---|---|---|
| GET/POST | `/api/v1/projects` | List / create |
| GET/PUT/DELETE | `/api/v1/projects/{project_id}` | Read / edit / delete project only |
| PUT | `/api/v1/history/{session_id}/project` | Assign/move/detach a session |

### Operator-authored project materials

History → select a project → **Project materials** stores an event brief,
handout text, agenda, speaker information, an explicitly user-authored decision,
or a user-authored TODO. Only TODOs have a completion checkbox. These are
operator-authored originals, not model findings or evidence that anyone spoke
or agreed to the text. The editor renders plain text, never executes Markdown
or HTML, and does not automatically call an external service.

Handouts support **UTF-8 TXT/Markdown text only** in this editor (64 KiB file,
20,000 Unicode characters); review the populated text before saving. PDF/DOCX
and original binary file storage are outside this material editor. The stored
original is the submitted text, including whitespace; the content SHA-256 is
over canonical UTF-8 JSON of `kind,title,text,done`, not a claim about source
file bytes. Editing replaces the current material with a new revision. Existing
digest snapshots retain their exact previous text and SHA until explicitly
deleted. Project limits are 100 materials and 120,000 text characters, with
20,000 characters per item. Exceeding limits fails without truncation.

Operator-only API under `/api/v1/projects/{project_id}/knowledge`:
`GET` lists the complete bounded scope, `POST` creates, and
`PUT /{knowledge_id}` edits with the current `expected_revision`.
`DELETE /{knowledge_id}?expected_revision=N` checks the same revision.
Concurrent stale edits/deletes return **409**; copy unsaved text and reload
before applying an intentional replacement. IDs, revision and content digest
are preserved by the full private backup/restore.

On explicit project digest generation, materials enter separate **untrusted
operator context** under the existing provider consent policy. They never enter
the spoken-source marker/citation pool. Their exact snapshot, revision and hash
participate in the digest input hash; additions, edits and TODO changes mark
old digest versions stale without changing approved text. Every model HTTP send
rechecks current materials, including hierarchical reduction steps; mutation
routes first cancel related jobs/prepared exports. An already transmitted HTTP
request cannot be recalled. The version viewer displays the frozen material text
separately from original speech.

Deleting a material permanently removes all project digest versions/reviews
whose frozen input contains it, in the same SQLite transaction. The confirmation
states this consequence before deletion. It preserves unrelated materials and
session transcripts, WAVs and session summaries. Project deletion cascades its
materials; detached sessions never acquire copies of these project-only notes.
Private backup **v3** adds the allowlisted `project_knowledge` graph table.
The reader checks original v1/v2 graph bytes/hash before explicitly supplying
an empty materials table; existing immutable digest snapshot JSON is untouched.
Old package formats cannot smuggle the new table. A digest snapshot may retain
an older revision only when its stable material ID still belongs to that package.

This completes the typed text-material slice of #86. It does not establish a
retention/legal policy, field approval, theological correctness, or audience
permission to read these private operator sources.

New session requests accept an optional `project_id`; old clients without the property remain compatible. Project association responses appear as nullable `project_id` on the historical SessionRecord.

## Deferred project knowledge layer

The initial project CRUD slice was tracked in #85. Cross-session search, cited
synthesis and full private package backup/restore are described below. #86 also
tracks the remaining knowledge/retention acceptance; these features do not imply
semantic or field approval. Per-session archive work is documented under #78–#81.

## Verification

```bash
uv run pytest -q apps/server/tests/test_event_projects.py
cd apps/web && npm ci && npm test && npm run build
```

A green exact-commit CI, security audit, and browser E2E are required before merge; test files being present does not establish that they passed.

## Cross-session transcript search (incremental project knowledge layer)

When a project is selected in **Session History**, **Search event transcripts** searches that project's **committed source captions and translated caption values** across all linked sessions. Each hit carries immutable `session_id`, `segment_id`, source timestamp, language and original text for evidence inspection. Choosing a hit opens its originating session and filters transcript entries.

API: `GET /api/v1/projects/{project_id}/search?q=term&limit=50&offset=0`

- The query is 2–128 characters; `limit` is 1–100, and `offset` is 0–10,000.
- All strings use SQL-bound parameters. Literal `%`, `_` and `!` are escaped; users cannot introduce SQL LIKE wildcards.
- Searches only rows with `committed=1`. Uncommitted/provisional speech is intentionally excluded.
- Query joins through `project_sessions` before inspecting transcript text, preventing retrieval from another event.
- Translation JSON is searched through SQLite JSON1 **values**; language keys alone cannot trigger false positives.
- As with every operator endpoint, browser Origins and loopback-only restrictions apply; audience join codes grant no search access.
- Keyword matching uses SQLite LIKE semantics (ASCII case-insensitive by default); this is **not** multilingual semantic search or vector retrieval.
- Project-wide AI synthesis and source-grounded multi-session summaries remain a separate follow-up under #86; an AI response is never treated as original transcript evidence.

All search results are dynamic views; session reassignment/detachment immediately changes the scope of subsequent searches without duplicating transcript content.


## Project-wide transcript search (stacked PR)

Select an event project in Session History, then use **Search event transcripts**.
The search considers only latest **committed** source segments and their existing
language translations, returning independent matches with `session_id`,
`segment_id`, speaker and millisecond offsets. Search results link back to
the original archived session rather than creating a second copy of a transcript.

- `GET /api/v1/projects/{project_id}/search?q=term&limit=50&offset=0`
- Requires the existing loopback-only operator API guard; not an audience route.
- Query length 2–128 characters; literal `%` / `_` are escaped and values are
  SQL parameters. Max 100 results per page and 10,000 offset.
- SQLite JSON1 searches translation **values** rather than keys. The initial
  index is bounded SQL scan within one project's sessions, not embedding search.
- Deleting or reassigning a session immediately removes it from the old project's
  search scope without changing transcript records.

**Limitations:** SQLite's built-in case-insensitive LIKE behavior is
ASCII-oriented; sophisticated Korean morphological search, fuzzy matching,
token ranking, and cross-session cited project-wide AI synthesis are deferred
to issue #86. Search does not alone prove summary correctness.


## Event archive index (manifest, separate stacked feature)

After session archive and live-recording support are present, the operator-only
`GET /api/v1/projects/{project_id}/archive?limit=25&offset=0` endpoint lists
existing event sessions, latest session-summary **provenance**, audio recording
IDs/lengths/SHA-256 hashes, and whether the *latest committed original transcript*
still matches the summary's original input SHA-256. Maximum page size is 100
sessions and maximum offset is 10,000.

The Event Archive Index panel offers a paginated project view and **Export current
page as JSON**. Export is metadata-only, without raw audio, transcript text,
filesystem paths, provider API keys or hidden application configuration. A summary
excerpt of at most 400 characters is included only for the local operator. The
manifest records its generation timestamp and session references for auditability,
not as a substitute for a backup.

This view **does not generate a project-wide AI narrative**. It consolidates
existing session outputs and flags stale inputs so that later project-level
summarization can cite verified sources. Because session moves/deletions can happen
while fetching an index, the manifest states a total count *at query time* rather
than a transactionally frozen archive. No completeness guarantee for concurrent
edits is implied. Use the frozen project digest and full private package features
below when those guarantees are needed.


### Evidence-linked Markdown working report

From the same Event Archive Index UI, **Download cited report (Markdown)**
calls the operator-only
`GET /api/v1/projects/{project_id}/report?limit=25&offset=0` endpoint.

The document includes project metadata, session IDs, starts, recording counts,
the existing latest summary status and excerpt (up to 5,000 characters),
the source-transcript SHA-256 used to create that summary, a **STALE** marker
when the committed transcript has changed, and up to three timestamped,
segment-ID-referenced **committed original-language** excerpts per session.

This is an audit-friendly **working report**, not a new cross-session LLM
summarization or a full export. Each request is bounded to at most 25 sessions;
pagination can change while sessions are edited/moved; untrusted model output
and transcript/title text are escaped before Markdown inclusion. The report
does not include binary recordings, translated captions, API credentials or
local recording filesystem paths. Treat even a hash-matching summary as
**unreviewed until a human checks its factual accuracy**.

## Project digest and chronology (#86, first synthesis slice)

The separate **Project digest · chronology** panel under the Event Archive Index
generates a versioned AI draft from the **whole project's completed sessions**,
not just the visible manifest page. Each finished session must first have a
completed, current, source-cited summary. Running sessions are explicitly excluded;
the panel shows their count and warns when interrupted/recovered sessions are included.
Missing/stale session summaries block generation with a preparation message.

One SQLite transaction freezes project/session metadata, the selected session
summaries and saved reviews, and their exact committed original or explicitly
approved offline transcript evidence. Sessions are ordered by their actual start
instant, then ID. A canonical SHA-256 records the complete input set, including
membership, summary/review identity and approved-source selection. Identical
inputs/provider/model/language/prompt return the existing completed version;
failure/retry retains separate attempts. There is no implicit regeneration.

The existing hierarchical summary provider, bounded HTTP reader, request deadlines
and endpoint transmission policy are reused. Session summaries remain untrusted
notes, never new spoken evidence. Only original source segments can be cited.
Project aliases such as `[p0001.s000001 at 12s]` prevent equal segment IDs in
different sessions from colliding; the frozen citation map retains the exact
session, original segment, source transcript version and time. Every included
session must have an original citation in the final draft and saved review.
The UI offers the frozen original evidence and links to its source sessions;
these links do not claim word-accurate playback alignment.

Generate explicitly, inspect the draft/provenance and original source, save review
drafts, then approve and lock a version. Original model content and append-only
reviews remain preserved. Project/source/summary-review changes, new sessions,
completion of a formerly running session and transcript approvals make old input
sets stale. Stale versions cannot be reviewed or approved; generate a new version.
A moved/detached source hides its old project version's text/evidence and blocks
export there. Deleting a source session deletes dependent project digests and
reviews too, so its frozen source is not retained as a hidden duplicate. Deleting
a project cascades digests/reviews while preserving the original sessions.

At most **25 project sessions**, **6,000 original segments** and **240,000 combined
original/note characters** are admitted. Exceeding a bound rejects the entire
operation; reduce the project scope rather than assuming a partial digest.
Each project retains at most 50 attempts and each version at most 100 reviews.
The global **two-summary-job** and **ten-minute generation** limits are shared
with per-session summaries. Generation is asynchronous; cancellation/shutdown
terminates the job, restart marks unfinished attempts failed for explicit retry.
An HTTP request cancelled during SQLite admission cannot leave an unmanaged
running row: an independent admission owner retains the shared slot and a native
thread cancellation fence until handoff or cleanup. Shutdown drains pending
admissions too, including repeated HTTP/shutdown cancellation. Inference happens
outside the snapshot transaction. Deleting a source session/project or moving or
detaching a source cancels and waits for related jobs before the API mutation.
Every provider request, including intermediate/reduction/final stages, rechecks
source existence and project membership; direct database changes also prevent
further sends. An already transmitted request cannot be recalled from a provider.
A source change
during inference preserves a stale draft; it cannot silently approve new evidence.

All endpoints below are local-operator/origin protected; audience codes grant no
access. The panel fails closed until `summary_external_providers` metadata is
verified. OpenAI-compatible endpoints and non-loopback Ollama require explicit
per-request transmission consent. Provider or project/endpoint changes, refresh
and generation clear consent. Historical provider/model labels do not infer the
past endpoint's location from current configuration. No cloud upload is implicit.

| Endpoint under `/api/v1/projects/{project_id}` | Meaning |
|---|---|
| `GET /digests` | Bounded version metadata, current input readiness/hash and transmission policy |
| `POST /digests` | Explicit async generation with the existing summary language/provider/model/consent keys |
| `GET /digests/{digest_id}` | Exact draft, saved review, frozen evidence/provenance and current stale state |
| `POST /digests/{digest_id}/cancel` | Cancel an owned running attempt; explicit retry creates another attempt |
| `POST /digests/{digest_id}/reviews` | Append cited text/approval using `expected_review_id` concurrency |
| `GET /digests/{digest_id}/export` | JSON of one version, its frozen evidence and review history |

Markdown export uses the saved review when present. This version export is
separate from the full private package below. Typed operator materials above are
implemented; retention/legal and field/semantic acceptance remain open under #86. Citation identity
and automated three-language/three-session tests do not certify model meaning,
human review quality, actual multilingual speech or field/device acceptance.

`test_project_digests.py` exercises actual SQLite, HTTP jobs, source identities,
staleness, cancellation/admission, privacy, deletion and hierarchical provider
requests. The browser runner creates two additional synthetic mock-ASR sessions,
generates all three per-session summaries through the real loopback-only fake
provider, then clicks the real project Generate/save/approve controls and checks
version export, provenance, source navigation, move redaction, mobile and axe.

### Full private project package backup and restore

The history workspace offers **Full private project backup and restore**, separate
from the paginated metadata index and one-version digest export. Choose a project,
finish all of its sessions/transcription/summary jobs, prepare its complete backup,
then use **Download private ZIP**. The browser downloads an attachment directly;
it does not buffer the entire archive into a JavaScript Blob. Restore also works
when history is empty: explicitly choose a local ZIP and click **Restore selected
backup**. Both actions require the local operator server and allowed browser
origin. The UI refuses non-loopback upload destinations. No model/provider is
called by backup or restore.

This is a **private plaintext backup** containing original speech text and WAV
audio, translations/provenance, all transcript versions/reviews/approvals, every
session summary/review, the project glossary and frozen project digest graph.
App settings/authentication/rate-limit tables are excluded. This does not promise
that arbitrary source text contains no confidential strings. Store the downloaded
file securely and restore only material you are authorized to use. A download
already delivered to the browser cannot be recalled by later deletion.

Version 3 is a ZIP_STORED archive with exactly `manifest.json`, `graph.json` and
the declared `media/<recording_id>.wav` entries. The graph has a fixed table and
column allowlist; no SQL or executable instructions are read from the package,
and package-supplied paths never determine a destination. A single SQLite read transaction collects the whole selected
project. Limits are **25 sessions, 50,000 total rows, 64 recordings, 32 MiB graph
JSON, 64 KiB manifest and 1 GiB total ZIP**; each recording retains the existing
512 MiB/four-hour WAV bounds. There is no pagination/truncation fallback. Oversize
projects must be reduced in scope. Missing/changed WAVs, ongoing work or a
historical digest referring to a source moved outside the project fail the entire
backup. Restore the source's original linkage before backing up that closed graph.

Version 3 includes operator materials and preserves nullable recording
`start_offset_ms` introduced in version 2. The reader still accepts original
version 1/2 packages: it verifies the original graph bytes/hash first, supplies
an empty materials table for both, and adds a null (unknown) recording offset
only for version 1. Raw immutable digest snapshot JSON and its input hashes are unchanged;
legacy audio is never assigned an invented zero offset.

Restore preserves stable project/session/recording/version/review IDs, the raw
JSON strings underlying immutable snapshots and SHA-256 provenance, timestamps
and append order. Local WAV filenames are newly generated safe managed names.
Historical stale summaries/digests remain stale; restore does not generate a new
approval. Any ID collision—including a not-yet-cleaned deleted recording ownership
tombstone—returns **409** without overwriting, remapping or merging existing data.
An empty application data profile is the recommended destination. Inactive
historical join codes stay session metadata; restore does not start a session,
install an audience authorization, import application credentials or reset current
authentication/rate-limit state.

The service prechecks the bounded ZIP directory before allocating its entry list,
rejects compression, encryption, ZIP64/multidisk, expansion ratios, duplicate or
unexpected paths and symlinks, then validates cumulative counters, closed graph
references, hashes and PCM WAV format before destination graph mutation. It
checks free-space reserve before writing the ZIP and staged recordings. Complete
WAV partials are flushed and verified before durable ownership is recorded. One
short write transaction rechecks collisions, promotes at most 64 owned files,
inserts the fixed graph and transfers recording ownership with a committed import
receipt. Large file hashing runs outside that transaction. POSIX directory entries
are fsynced at preparation/promotion; Windows uses flushed file handles and native
no-replace rename semantics. Filesystem/device failure may still require repair;
no power-loss guarantee beyond those OS primitives is claimed.

One native I/O slot, 600-second native-call deadlines, a 15-minute operation lease,
chunk-level stop fences and independently owned cleanup prevent cancellation from
overlapping a still-running writer. Upload disconnect/shutdown/discard and source
project/session move/delete stop related operations; bytes already sent remain
outside server control. Prepared exports are removed after download or expiry.
Restart recovery reuses the single bounded recording recovery worker, processes
one package owner per batch, and hashes at most the package's declared bounds
with a shutdown fence. Failed precommit imports remove only verified owned files;
committed receipts protect restored recordings after a lost HTTP acknowledgement.
Integrity mismatch quarantines its ownership record and preserves the file for
repair. Unknown WAVs/symlinks are never inferred as imported assets or removed.
Unjournaled service-named partials retain the existing 24-hour grace policy;
journal-owned partials are excluded. Recent completed restore receipts are visible
through status refresh so a lost acknowledgement does not invite an unsafe retry.

| Endpoint | Behavior |
| --- | --- |
| `POST /api/v1/projects/{project_id}/package` | Prepare the whole bounded private ZIP; return opaque export ID, bytes and SHA-256 |
| `GET /api/v1/project-packages/{export_id}/download` | Native attachment, single download, no-store |
| `DELETE /api/v1/project-packages/{export_id}` | Discard the owned prepared export |
| `POST /api/v1/project-packages/restore` | Stream an explicitly selected ZIP; validate and atomically restore |
| `GET /api/v1/project-packages` | Prepared lease, up to 20 recent committed import receipts and up to 20 generic quarantine repair records, without file paths |

`test_project_package.py` and `test_project_package_api.py` use actual SQLite,
synthetic WAVs and ZIP streams, including three-language/multiple-recording exact
graph roundtrip, replacement-approval stale history, collisions, hostile inputs,
late native cancellation, disconnect and scope deletion. Actual owned subprocess
termination before/after graph commit verifies restart rollback versus committed
receipt recovery. The browser smoke downloads the native ZIP, checks its SHA,
rejects a duplicate restore, removes only its synthetic fixture sources, restores
through the real file-picker/UI/API and verifies stable IDs, original WAV hashes,
frozen digest/review bytes, inactive runtime, mobile layout and accessibility.
These storage/service tests do not certify semantic model quality or field use.
The actual React harness separately covers delayed JSON/project/endpoint responses,
unknown metadata recovery, provider approval resets, same-ID completion and cancel.
No model weights or private speech are used by these regressions.
