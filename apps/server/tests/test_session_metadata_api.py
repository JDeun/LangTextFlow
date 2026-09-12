from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from langtextflow import main
from langtextflow.models import (
    ProductPreset,
    SessionContext,
    SessionDetail,
    SessionMetadataUpdate,
    SessionState,
)


class FakeHistory:
    def __init__(self) -> None:
        self.updated: tuple[str, str, str] | None = None

    def update_metadata(self, session_id: str, *, title: str, notes: str) -> bool:
        self.updated = (session_id, title, notes)
        return True

    def get_session(self, session_id: str) -> SessionDetail:
        assert self.updated is not None
        _, title, notes = self.updated
        return SessionDetail(
            session_id=session_id,
            join_code="ABC123",
            title=title,
            notes=notes,
            presenter=None,
            preset=ProductPreset.GENERAL,
            source_language="ko",
            target_languages=["en"],
            engine="mock",
            translation_provider="none",
            translation_model=None,
            started_at="2026-09-12T00:00:00+00:00",
            ended_at="2026-09-12T01:00:00+00:00",
            segment_count=0,
            context=SessionContext(title="Original live title"),
        )


def local_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_history_metadata_update_returns_updated_archive(monkeypatch) -> None:
    history = FakeHistory()
    fake_runtime = SimpleNamespace(
        state=SessionState(session_id=None, running=False),
        history=history,
    )
    monkeypatch.setattr(main, "runtime", fake_runtime)

    result = await main.update_history_session(
        local_request(),
        "session-1",
        SessionMetadataUpdate(title=" Reviewed session ", notes=" Follow up "),
    )

    assert result.title == "Reviewed session"
    assert result.notes == "Follow up"
    assert result.context.title == "Original live title"
    assert history.updated == ("session-1", "Reviewed session", "Follow up")


@pytest.mark.asyncio
async def test_history_metadata_update_rejects_active_session(monkeypatch) -> None:
    history = FakeHistory()
    fake_runtime = SimpleNamespace(
        state=SessionState(session_id="session-1", running=True),
        history=history,
    )
    monkeypatch.setattr(main, "runtime", fake_runtime)

    with pytest.raises(HTTPException) as exc_info:
        await main.update_history_session(
            local_request(),
            "session-1",
            SessionMetadataUpdate(title="Do not edit live", notes=""),
        )

    assert exc_info.value.status_code == 409
    assert history.updated is None
