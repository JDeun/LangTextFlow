"""Actual existing Python bootstrap/read/export/private-restore contracts, synthetic only."""
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "apps/server"), str(ROOT / "apps/server/tests")]

from langtextflow.models import SessionState, StartSessionRequest  # noqa: E402
from langtextflow.post_event_archive import PostEventArchive  # noqa: E402
from langtextflow.project_glossary_repository import ProjectGlossaryRepository  # noqa: E402
from langtextflow.project_repository import ProjectInput, ProjectRepository  # noqa: E402
from langtextflow.session_repository import SessionRepository  # noqa: E402

mode, directory = sys.argv[1], Path(sys.argv[2])
db = directory / "history.db"
history = SessionRepository(str(db))

if mode in {"setup", "legacy"}:
    history.initialize()
    projects = ProjectRepository(str(db))
    project = projects.create(ProjectInput(title="Rust SQLite synthetic parity"))
    archive = PostEventArchive(str(db))
    archive.initialize()
    ProjectGlossaryRepository(str(db)).initialize()
    history.create_session(
        SessionState(session_id="history-session", join_code="TEST12", engine="mock",
                     started_at=datetime(2026, 1, 1, tzinfo=UTC)),
        StartSessionRequest(engine="mock", source_language="en", target_languages=["ja", "ko"],
                            project_id=project.project_id, translation_provider="m2m100-ct2"),
    )
    if mode == "legacy":
        from langtextflow.models import TranscriptEvent

        history.upsert_segment("history-session", TranscriptEvent(
            segment_id="legacy", version=1, stage="committed", committed=True,
            source_language="en", text="Legacy corrected display", start_ms=0, end_ms=1000,
        ))
        with sqlite3.connect(db) as connection:
            for column in ("source_revision", "original_text", "translation_states_json",
                           "asr_provenance_json", "translation_provenance_json"):
                connection.execute(f"ALTER TABLE transcript_segments DROP COLUMN {column}")
elif mode == "migrate":
    history.initialize()
    original = history.segments("history-session")[0]
    assert original.original_text is None and original.asr_provenance is None
    assert original.translation_provenance == {} and original.translation_states == {}
    assert original.text == "Legacy corrected display" and original.source_revision == 1
elif mode == "sql":
    import ast
    import inspect
    import textwrap

    method = ast.parse(textwrap.dedent(inspect.getsource(SessionRepository.upsert_segment)))
    print(next(node.value for node in ast.walk(method) if isinstance(node, ast.Constant)
               and isinstance(node.value, str)
               and "INSERT INTO transcript_segments (" in node.value))
elif mode == "verify":
    from fastapi.testclient import TestClient
    from test_project_package import empty, export, restore

    from langtextflow.models import TranscriptEvent

    # Open a new repository/process view; never substitute a mirror persistence implementation.
    observed = json.loads((directory / "observed.json").read_text(encoding="utf-8"))
    expected = TranscriptEvent.model_validate(observed)
    events = history.segments("history-session")
    assert len(events) == 1 and events[0] == expected
    session = history.get_session("history-session")
    assert session.segment_count == 1 and session.ended_at is not None
    assert session.started_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert events[0].original_text == "Original source 한글 日本語"
    assert events[0].translation_states == {"ja": "available", "ko": "failed"}
    assert events[0].translations == {"ja": "Japanese target 日本語"}
    assert events[0].asr_provenance.timestamp_basis == "sample_clock_native_hint"
    assert events[0].translation_provenance["ja"].verified_model is None

    os.environ.update(LANGTEXTFLOW_DATABASE_PATH=str(db), LANGTEXTFLOW_MDNS_ENABLED="false")
    from langtextflow import main

    with TestClient(main.app, client=("127.0.0.1", 4900)) as client:
        captions = client.get("/api/v1/history/history-session/captions")
        assert captions.status_code == 200
        assert TranscriptEvent.model_validate(captions.json()[0]) == expected
        exported = client.get("/api/v1/history/history-session/export?format=json")
        assert exported.status_code == 200
        assert exported.json()["segments"][0]["original_text"] == "Original source 한글 日本語"
        subtitle = client.get("/api/v1/history/history-session/export?format=srt&lang=ja")
        assert subtitle.status_code == 200 and "Japanese target 日本語" in subtitle.text
    archive = PostEventArchive(str(db))
    archive.initialize()
    pid = session.project_id
    raw, graph = export(archive, pid)
    restored, projects, target = empty(directory / "restored")
    receipt = restore(target, raw)
    assert receipt["sessions"] == 1 and projects.get(pid)
    assert restored.segments("history-session") == events
    print(json.dumps({"python_repository": True, "http_history": True,
                      "export": True, "private_backup_restore": True}))
else:
    raise AssertionError("unknown test mode")
