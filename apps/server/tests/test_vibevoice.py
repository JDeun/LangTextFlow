from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine, vibevoice_ws_url
from langtextflow.models import GlossaryEntry, SessionContext, StartSessionRequest


def test_vibevoice_websocket_url() -> None:
    assert vibevoice_ws_url("http://127.0.0.1:8001") == "ws://127.0.0.1:8001/v1/stream"
    assert vibevoice_ws_url("https://asr.example.com/") == "wss://asr.example.com/v1/stream"


def test_vibevoice_context_includes_domain_terms() -> None:
    request = StartSessionRequest(
        context=SessionContext(
            title="Mission Conference",
            presenter="John Smith",
            hotwords=["요한복음"],
            glossary=[GlossaryEntry(term="칭의", aliases=["의롭다 하심"])],
        )
    )
    value = VibeVoiceStreamingAsrEngine._context_info(request)
    assert value is not None
    assert "Mission Conference" in value
    assert "John Smith" in value
    assert "요한복음" in value
    assert "칭의" in value
    assert "의롭다 하심" in value
