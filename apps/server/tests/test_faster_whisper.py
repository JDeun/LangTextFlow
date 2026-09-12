from __future__ import annotations

from array import array

import pytest

from langtextflow.asr.faster_whisper import DecodedSegment, FasterWhisperStreamingAsrEngine
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest


def pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    count = int(seconds * sample_rate)
    return array("f", [0.1] * count).tobytes()


class FakeFasterWhisperEngine(FasterWhisperStreamingAsrEngine):
    def __init__(self, publish, **kwargs) -> None:
        super().__init__(publish, **kwargs)
        self.decoded_sizes: list[int] = []

    def _load_model_sync(self):
        return object()

    def _transcribe_chunk_sync(self, pcm_f32le: bytes, request: StartSessionRequest):
        del request
        self.decoded_sizes.append(len(pcm_f32le))
        duration = len(pcm_f32le) / 4 / self.sample_rate
        return [
            DecodedSegment(
                start_seconds=0.0,
                end_seconds=duration,
                text=f"chunk-{len(self.decoded_sizes)}",
            )
        ]


@pytest.mark.asyncio
async def test_faster_whisper_micro_batches_and_flushes_residual_audio() -> None:
    published = []

    async def publish(event) -> None:
        published.append(event)

    engine = FakeFasterWhisperEngine(
        publish,
        chunk_seconds=0.1,
        queue_chunks=4,
    )
    request = StartSessionRequest(
        source_language="ko",
        engine="faster-whisper",
        context=SessionContext(title="Fallback test", hotwords=["요한복음"]),
    )

    await engine.start(request)
    assert engine.accepts_audio is True
    assert engine.sample_rate == 16000
    assert engine.queue_capacity == 4

    await engine.feed_audio(pcm(0.05))
    await engine.feed_audio(pcm(0.05))
    await engine.feed_audio(pcm(0.05))
    await engine.stop()

    assert len(engine.decoded_sizes) == 2
    assert len(published) == 2
    assert all(event.stage is CaptionStage.STABLE for event in published)
    assert published[0].segment_id == "fw-00000001"
    assert published[0].start_ms == 0
    assert published[0].end_ms == 100
    assert published[1].start_ms == 100
    assert published[1].end_ms == 150
    assert published[1].text == "chunk-2"


def test_faster_whisper_rejects_invalid_chunk_duration() -> None:
    async def publish(event) -> None:
        del event

    with pytest.raises(ValueError, match="chunk_seconds"):
        FasterWhisperStreamingAsrEngine(publish, chunk_seconds=0)
