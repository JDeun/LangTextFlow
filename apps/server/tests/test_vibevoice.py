import asyncio
import json

import pytest

from langtextflow.asr.base import AsrEngineError
from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine, vibevoice_ws_url
from langtextflow.models import CaptionStage, GlossaryEntry, SessionContext, StartSessionRequest


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


class FakeResponse:
    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, object]:
        return {"sample_rate": 16000, "chunk_seconds": 2.0}


class FakeHttpClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def get(self, url: str) -> FakeResponse:
        assert url.endswith("/v1/config")
        return FakeResponse()


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.closed = False

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def send(self, payload: str | bytes) -> None:
        self.sent.append(payload)
        if isinstance(payload, bytes):
            await self.incoming.put(json.dumps({"text": "오늘 말씀을 시작하겠습니다"}))
        elif payload == "end":
            await self.incoming.put(json.dumps({"done": True, "total_chunks": 1}))
            await self.incoming.put(None)

    async def close(self) -> None:
        self.closed = True
        await self.incoming.put(None)


async def _start_engine(
    monkeypatch: pytest.MonkeyPatch,
    fake_ws: FakeWebSocket,
) -> VibeVoiceStreamingAsrEngine:
    async def publish(event) -> None:
        del event

    async def fake_connect(url: str, **kwargs: object) -> FakeWebSocket:
        assert url == "ws://127.0.0.1:8001/v1/stream"
        assert kwargs["max_size"] is None
        return fake_ws

    monkeypatch.setattr("langtextflow.asr.vibevoice.httpx.AsyncClient", FakeHttpClient)
    monkeypatch.setattr("langtextflow.asr.vibevoice.websockets.connect", fake_connect)

    engine = VibeVoiceStreamingAsrEngine(
        publish,
        base_url="http://127.0.0.1:8001",
        queue_chunks=2,
    )
    await engine.start(
        StartSessionRequest(
            source_language="ko",
            engine="vibevoice",
            context=SessionContext(title="Mission Conference", hotwords=["요한복음"]),
        )
    )
    return engine


@pytest.mark.asyncio
async def test_vibevoice_streaming_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    published = []
    published_event = asyncio.Event()
    fake_ws = FakeWebSocket()

    async def publish(event) -> None:
        published.append(event)
        published_event.set()

    async def fake_connect(url: str, **kwargs: object) -> FakeWebSocket:
        assert url == "ws://127.0.0.1:8001/v1/stream"
        assert kwargs["max_size"] is None
        return fake_ws

    monkeypatch.setattr("langtextflow.asr.vibevoice.httpx.AsyncClient", FakeHttpClient)
    monkeypatch.setattr("langtextflow.asr.vibevoice.websockets.connect", fake_connect)

    engine = VibeVoiceStreamingAsrEngine(
        publish,
        base_url="http://127.0.0.1:8001",
        queue_chunks=2,
    )
    request = StartSessionRequest(
        source_language="ko",
        engine="vibevoice",
        context=SessionContext(title="Mission Conference", hotwords=["요한복음"]),
    )

    await engine.start(request)
    assert engine.sample_rate == 16000
    assert engine.accepts_audio is True
    assert engine.failure is None

    init_message = json.loads(str(fake_ws.sent[0]))
    assert "Mission Conference" in init_message["context_info"]
    assert "요한복음" in init_message["context_info"]

    await engine.feed_audio(b"\x00\x00\x00\x00" * 400)
    await asyncio.wait_for(published_event.wait(), timeout=1.0)

    assert len(published) == 1
    assert published[0].stage is CaptionStage.STABLE
    assert published[0].text == "오늘 말씀을 시작하겠습니다"
    assert published[0].source_language == "ko"

    await engine.stop()
    assert fake_ws.closed is True


@pytest.mark.asyncio
async def test_vibevoice_records_unexpected_receiver_close_and_rejects_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ws = FakeWebSocket()
    engine = await _start_engine(monkeypatch, fake_ws)

    # Remote socket ends without the protocol-level `done` message.
    await fake_ws.incoming.put(None)
    for _ in range(20):
        if engine.failure is not None:
            break
        await asyncio.sleep(0)

    assert engine.running is False
    assert engine.failure is not None
    assert "stream closed unexpectedly" in engine.failure
    with pytest.raises(AsrEngineError, match="stream closed unexpectedly"):
        await engine.feed_audio(b"\x00\x00\x00\x00")

    await engine.stop()
    assert fake_ws.closed is True
