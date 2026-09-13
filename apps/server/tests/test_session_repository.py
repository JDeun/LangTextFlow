import sqlite3
from datetime import UTC, datetime

from langtextflow.models import (
    CaptionStage,
    CorrectionProvenance,
    SessionContext,
    SessionState,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.session_repository import SessionRepository


def _state() -> SessionState:
    return SessionState(
        session_id="session-1",
        join_code="ABC123",
        running=True,
        source_language="ko",
        target_languages=["en"],
        engine="mock",
        context=SessionContext(title="Mission Meeting"),
        started_at=datetime(2026, 9, 12, 0, 0, tzinfo=UTC),
    )


def _request() -> StartSessionRequest:
    return StartSessionRequest(
        source_language="ko",
        target_languages=["en"],
        engine="mock",
        correction_provider="ollama",
        correction_model="qwen3.5:4b",
        translation_provider="demo",
        context=SessionContext(title="Mission Meeting"),
    )


def test_session_history_keeps_latest_segment_version_and_correction_provenance(
    tmp_path,
) -> None:
    repository = SessionRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create_session(_state(), _request())

    repository.upsert_segment(
        "session-1",
        TranscriptEvent(
            segment_id="seg-1",
            version=1,
            stage=CaptionStage.STABLE,
            source_language="ko",
            text="요한 보금 3장",
            start_ms=1000,
            end_ms=4000,
        ),
    )
    provenance = CorrectionProvenance(
        method="llm",
        provider="ollama",
        model="qwen3.5:4b",
        deterministic_changed=True,
        llm_attempted=True,
        llm_applied=True,
        changed=True,
    )
    repository.upsert_segment(
        "session-1",
        TranscriptEvent(
            segment_id="seg-1",
            version=4,
            stage=CaptionStage.COMMITTED,
            source_language="ko",
            text="요한복음 3장",
            translations={"en": "John chapter 3"},
            start_ms=1000,
            end_ms=4000,
            correction=provenance,
            committed=True,
        ),
    )
    repository.upsert_segment(
        "session-1",
        TranscriptEvent(
            segment_id="seg-1",
            version=2,
            stage=CaptionStage.CORRECTED,
            source_language="ko",
            text="stale",
            start_ms=1000,
            end_ms=4000,
        ),
    )

    segments = repository.segments("session-1")
    assert len(segments) == 1
    assert segments[0].version == 4
    assert segments[0].committed is True
    assert segments[0].translations["en"] == "John chapter 3"
    assert segments[0].correction == provenance

    sessions = repository.list_sessions()
    assert sessions[0].segment_count == 1
    assert sessions[0].title == "Mission Meeting"
    assert sessions[0].notes == ""
    assert sessions[0].correction_provider == "ollama"
    assert sessions[0].correction_model == "qwen3.5:4b"
    assert sessions[0].interrupted is False


def test_session_history_can_record_resolved_asr_provider(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    state = _state().model_copy(update={"engine": "auto"})
    request = _request().model_copy(update={"engine": "auto"})
    repository.create_session(state, request)

    repository.update_engine("session-1", "faster-whisper")
    detail = repository.get_session("session-1")

    assert detail is not None
    assert detail.engine == "faster-whisper"


def test_session_metadata_edit_preserves_original_context_snapshot(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create_session(_state(), _request())

    assert repository.update_metadata(
        "session-1",
        title="2026 Mission Meeting - reviewed",
        notes="John 3:16 translation checked. Follow up on speaker label.",
    )

    detail = repository.get_session("session-1")
    assert detail is not None
    assert detail.title == "2026 Mission Meeting - reviewed"
    assert detail.notes.startswith("John 3:16")
    assert detail.context.title == "Mission Meeting"


def test_initialize_migrates_existing_sessions_table_with_history_fields(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                join_code TEXT NOT NULL,
                title TEXT NOT NULL,
                presenter TEXT,
                preset TEXT NOT NULL,
                source_language TEXT NOT NULL,
                target_languages_json TEXT NOT NULL,
                engine TEXT NOT NULL,
                translation_provider TEXT NOT NULL,
                translation_model TEXT,
                context_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE transcript_segments (
                session_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                stage TEXT NOT NULL,
                source_language TEXT NOT NULL,
                text TEXT NOT NULL,
                translations_json TEXT NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER,
                speaker TEXT,
                confidence REAL,
                committed INTEGER NOT NULL,
                emitted_at TEXT NOT NULL,
                PRIMARY KEY (session_id, segment_id)
            )
            """
        )

    repository = SessionRepository(str(database_path))
    repository.initialize()

    with sqlite3.connect(database_path) as connection:
        session_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)")
        }
        segment_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(transcript_segments)")
        }
    assert "notes" in session_columns
    assert "correction_provider" in session_columns
    assert "correction_model" in session_columns
    assert "interrupted" in session_columns
    assert "correction_json" in segment_columns


def test_recover_interrupted_sessions_marks_only_unclosed_sessions(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create_session(_state(), _request())

    second_state = _state().model_copy(
        update={
            "session_id": "session-2",
            "join_code": "DEF456",
            "started_at": datetime(2026, 9, 12, 0, 30, tzinfo=UTC),
        }
    )
    repository.create_session(second_state, _request())
    repository.mark_ended("session-2", datetime(2026, 9, 12, 1, 0, tzinfo=UTC))

    recovered_at = datetime(2026, 9, 12, 2, 0, tzinfo=UTC)
    assert repository.recover_interrupted_sessions(recovered_at) == 1

    interrupted = repository.get_session("session-1")
    clean = repository.get_session("session-2")
    assert interrupted is not None
    assert interrupted.interrupted is True
    assert interrupted.ended_at == recovered_at
    assert clean is not None
    assert clean.interrupted is False
    assert clean.ended_at == datetime(2026, 9, 12, 1, 0, tzinfo=UTC)


def test_session_end_and_delete_cascades_transcript(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create_session(_state(), _request())
    repository.upsert_segment(
        "session-1",
        TranscriptEvent(
            segment_id="seg-1",
            version=1,
            stage=CaptionStage.STABLE,
            source_language="ko",
            text="test",
            start_ms=0,
        ),
    )

    repository.mark_ended("session-1", datetime(2026, 9, 12, 1, 0, tzinfo=UTC))
    detail = repository.get_session("session-1")
    assert detail is not None
    assert detail.ended_at is not None
    assert detail.interrupted is False

    assert repository.delete_session("session-1") is True
    assert repository.get_session("session-1") is None
    assert repository.segments("session-1") == []
