from datetime import UTC, datetime

from langtextflow.models import (
    CaptionStage,
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
        translation_provider="demo",
        context=SessionContext(title="Mission Meeting"),
    )


def test_session_history_keeps_latest_segment_version(tmp_path) -> None:
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
            committed=True,
        ),
    )
    # An out-of-order stale event must not replace the committed latest version.
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

    sessions = repository.list_sessions()
    assert sessions[0].segment_count == 1
    assert sessions[0].title == "Mission Meeting"


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

    assert repository.delete_session("session-1") is True
    assert repository.get_session("session-1") is None
    assert repository.segments("session-1") == []
