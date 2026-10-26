"""SQLite retains the latest source epoch and invalidates obsolete translations."""

import sqlite3

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.session_repository import SessionRepository
from test_session_repository import _request, _state


def event(version: int, revision: int, stage: CaptionStage, text: str,
          translations: dict[str, str] | None = None) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="revised", version=version, source_revision=revision,
        stage=stage, source_language="ko", text=text,
        translations=translations or {}, start_ms=0,
        committed=stage is CaptionStage.COMMITTED,
    )


def test_revision_replaces_corrected_snapshot_and_survives_restart(tmp_path) -> None:
    path = str(tmp_path / "sessions.db")
    repository = SessionRepository(path)
    repository.initialize()
    repository.create_session(_state(), _request())
    repository.upsert_segment("session-1", event(1, 1, CaptionStage.STABLE, "처음"))
    repository.upsert_segment(
        "session-1", event(2, 1, CaptionStage.CORRECTED, "교정")
    )
    repository.upsert_segment(
        "session-1", event(3, 1, CaptionStage.TRANSLATED, "교정", {"en": "first"})
    )
    repository.upsert_segment("session-1", event(4, 2, CaptionStage.STABLE, "수정"))
    restored = SessionRepository(path)
    assert restored.segments("session-1")[0].source_revision == 2
    assert restored.segments("session-1")[0].translations == {}
    restored.upsert_segment(
        "session-1", event(5, 1, CaptionStage.COMMITTED, "낡은", {"en": "stale"})
    )
    assert restored.segments("session-1")[0].text == "수정"
    restored.upsert_segment(
        "session-1", event(5, 2, CaptionStage.COMMITTED, "완료", {"en": "new"})
    )
    assert restored.segments("session-1")[0].translations == {"en": "new"}


def test_legacy_schema_migrates_source_revision_without_data_loss(tmp_path) -> None:
    path = str(tmp_path / "sessions.db")
    repository = SessionRepository(path)
    repository.initialize()
    repository.create_session(_state(), _request())
    repository.upsert_segment("session-1", event(1, 1, CaptionStage.STABLE, "기존"))
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE transcript_segments RENAME TO transcript_old")
        connection.execute(
            "CREATE TABLE transcript_segments AS SELECT session_id, segment_id, "
            "version, stage, source_language, text, translations_json, "
            "translation_states_json, start_ms, end_ms, speaker, confidence, "
            "correction_json, committed, emitted_at FROM transcript_old"
        )
        connection.execute("DROP TABLE transcript_old")
    repository.initialize()
    assert repository.segments("session-1")[0].source_revision == 1
