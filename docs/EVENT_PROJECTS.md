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

New session requests accept an optional `project_id`; old clients without the property remain compatible. Project association responses appear as nullable `project_id` on the historical SessionRecord.

## Deferred project knowledge layer

Cross-session full-text search, project-wide timelines, cited AI synthesis, aggregate recording packages, import/export and sharing policy are **not implemented in this initial PR**. Tracked in #86. Project CRUD and session organization are tracked in #85. Per-session recording and summarization remain separate work under #78-#81.

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
edits is implied. Project-level backup/export with binary media and full
cross-session synthesis remain issue #86.
