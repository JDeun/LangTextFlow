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
