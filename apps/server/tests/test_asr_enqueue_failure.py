"""A failed ASR consumer must release an audio producer blocked by backpressure."""

import asyncio

import pytest
from test_faster_whisper import FakeFasterWhisperEngine, wait_for_failure
from test_vibevoice import FakeHttpClient, FakeWebSocket

from langtextflow.asr.base import AsrEngineError
from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine
from langtextflow.models import StartSessionRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["faster-whisper", "vibevoice"])
@pytest.mark.parametrize("cancel_producer", [False, True])
async def test_blocked_audio_put_ends_with_consumer_and_leaves_no_orphan_frame(
    monkeypatch, provider, cancel_producer,
):
    entered = asyncio.Event()
    fail = asyncio.Event()

    async def publish(event):
        pass

    class FailingWhisper(FakeFasterWhisperEngine):
        async def _process_chunk(self, pcm):
            entered.set()
            await fail.wait()
            raise RuntimeError("consumer failed under backpressure")

    class FailingSocket(FakeWebSocket):
        async def send(self, data):
            if isinstance(data, bytes):
                entered.set()
                await fail.wait()
                raise RuntimeError("consumer failed under backpressure")
            await super().send(data)

    if provider == "faster-whisper":
        engine = FailingWhisper(publish, chunk_seconds=0.001, queue_chunks=1)
    else:
        socket = FailingSocket()

        async def connect(*args, **kwargs):
            return socket

        monkeypatch.setattr("langtextflow.asr.vibevoice.httpx.AsyncClient", FakeHttpClient)
        monkeypatch.setattr("langtextflow.asr.vibevoice.websockets.connect", connect)
        engine = VibeVoiceStreamingAsrEngine(
            publish, base_url="http://127.0.0.1:8001", queue_chunks=1,
        )

    frame = b"\0\0\0\0" * 16
    await engine.start(StartSessionRequest(engine=provider))
    pending = None
    try:
        await engine.feed_audio(frame)
        await asyncio.wait_for(entered.wait(), 1)
        await engine.feed_audio(frame)
        pending = asyncio.create_task(engine.feed_audio(frame))
        await asyncio.sleep(0)
        assert not pending.done()
        if cancel_producer:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        fail.set()
        if not cancel_producer:
            with pytest.raises(AsrEngineError, match="consumer failed"):
                await asyncio.wait_for(pending, 1)
        await wait_for_failure(engine)
        assert engine.queue_depth == 1  # Only the second, already accepted frame remains.
    finally:
        fail.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await engine.stop()
