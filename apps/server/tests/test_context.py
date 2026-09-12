import pytest

from langtextflow.models import GlossaryEntry, SessionContext, StartSessionRequest
from langtextflow.runtime import CaptionRuntime


def test_context_builds_deduplicated_asr_hotwords() -> None:
    context = SessionContext(
        hotwords=["요한복음", " 바울 ", "요한복음"],
        glossary=[
            GlossaryEntry(term="칭의", aliases=["의롭다 하심", "칭의"]),
            GlossaryEntry(term="성화", enabled=False),
        ],
    )

    assert context.asr_hotwords() == ["요한복음", "바울", "칭의", "의롭다 하심"]


@pytest.mark.asyncio
async def test_runtime_creates_audience_join_session() -> None:
    runtime = CaptionRuntime()
    state = await runtime.start(
        StartSessionRequest(
            context=SessionContext(title="Mission Conference", presenter="John Smith")
        )
    )
    try:
        assert state.session_id
        assert state.join_code
        view = runtime.audience_view(state.join_code.lower())
        assert view.title == "Mission Conference"
        assert view.presenter == "John Smith"
    finally:
        await runtime.stop()


def test_invalid_audience_code_is_rejected() -> None:
    runtime = CaptionRuntime()
    with pytest.raises(KeyError):
        runtime.audience_view("BAD999")
