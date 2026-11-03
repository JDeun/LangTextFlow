from __future__ import annotations

import asyncio
import sys
from array import array
from types import SimpleNamespace

import pytest

from langtextflow.asr.base import AsrEngineError
from langtextflow.asr.faster_whisper import DecodedSegment, FasterWhisperStreamingAsrEngine
from langtextflow.models import CaptionStage, SessionContext, StartSessionRequest


def pcm(seconds: float, sample_rate: int = 16000) -> bytes:
    count = int(seconds * sample_rate)
    return array("f", [0.1] * count).tobytes()


async def wait_for_failure(
    engine: FasterWhisperStreamingAsrEngine,
    *,
    timeout: float = 1.0,
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if engine.failure is not None:
            return engine.failure
        await asyncio.sleep(0.001)
    raise AssertionError("faster-whisper worker failure was not observed before timeout")


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


class FailingFasterWhisperEngine(FakeFasterWhisperEngine):
    def _transcribe_chunk_sync(self, pcm_f32le: bytes, request: StartSessionRequest):
        del pcm_f32le, request
        raise RuntimeError("GPU lost")


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


@pytest.mark.asyncio
async def test_faster_whisper_records_worker_failure_and_rejects_more_audio() -> None:
    async def publish(event) -> None:
        del event

    engine = FailingFasterWhisperEngine(publish, chunk_seconds=0.05, queue_chunks=2)
    await engine.start(StartSessionRequest(engine="faster-whisper"))
    await engine.feed_audio(pcm(0.05))

    failure = await wait_for_failure(engine)

    assert engine.running is False
    assert "GPU lost" in failure
    with pytest.raises(AsrEngineError, match="GPU lost"):
        await engine.feed_audio(pcm(0.01))
    await engine.stop()


def test_faster_whisper_rejects_invalid_chunk_duration() -> None:
    async def publish(event) -> None:
        del event

    with pytest.raises(ValueError, match="chunk_seconds"):
        FasterWhisperStreamingAsrEngine(publish, chunk_seconds=0)


@pytest.mark.asyncio
async def test_faster_whisper_end_audio_does_not_block_on_full_queue() -> None:
    async def publish(event) -> None:
        del event

    engine = FasterWhisperStreamingAsrEngine(publish, queue_chunks=1)
    engine._queue = asyncio.Queue(maxsize=1)
    engine._queue.put_nowait(b"\x00\x00\x00\x00")

    await asyncio.wait_for(engine.end_audio(), timeout=0.1)
    assert engine._ended is True

class ImpossibleTimestampEngine(FakeFasterWhisperEngine):
    def _transcribe_chunk_sync(self, pcm_f32le: bytes, request: StartSessionRequest):
        del request
        duration = len(pcm_f32le) / 4 / self.sample_rate
        return [DecodedSegment(
            start_seconds=0.0,
            end_seconds=duration + 20.0,
            text="hallucinated source timestamp",
        )]


@pytest.mark.asyncio
async def test_faster_whisper_rejects_decoder_timestamps_beyond_source_audio() -> None:
    published = []

    async def publish(event) -> None:
        published.append(event)

    engine = ImpossibleTimestampEngine(publish, chunk_seconds=0.1)
    await engine.start(StartSessionRequest(engine="faster-whisper"))
    await engine.feed_audio(pcm(0.1))
    failure = await wait_for_failure(engine)
    assert "out-of-bounds" in failure
    assert not published
    await engine.stop()



@pytest.mark.asyncio
async def test_faster_whisper_uses_integer_pcm_clock_across_many_chunks() -> None:
    published = []

    async def publish(event) -> None:
        published.append(event)

    engine = FakeFasterWhisperEngine(publish, chunk_seconds=0.001)
    await engine.start(StartSessionRequest(engine="faster-whisper"))
    # 16 samples per chunk: one millisecond at 16 kHz. Repeated chunks
    # must not accumulate fractional floating-point timeline drift.
    for _ in range(100):
        await engine._process_chunk(pcm(0.001))
    assert engine._audio_cursor_samples == 1600
    assert published[0].start_ms == 0
    assert published[-1].end_ms == 100
    assert all(
        earlier.end_ms <= later.start_ms
        for earlier, later in zip(published, published[1:], strict=False)
    )
    await engine.stop()


def native_timestamp_engine(monkeypatch, publish, *, start=0.85, end=4.85, info=None):
    # Keep the base test suite independent of the optional numpy/Whisper install.
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(
        float32=float,
        frombuffer=lambda audio, **_: SimpleNamespace(astype=lambda *_, **__: audio),
    ))

    def transcribe(audio, **options):
        assert audio == pcm(4)
        assert options["vad_filter"] is True
        return iter([SimpleNamespace(start=start, end=end, text=" source speech ")]), info

    engine = FasterWhisperStreamingAsrEngine(publish)
    engine._model = SimpleNamespace(transcribe=transcribe)
    engine._request = StartSessionRequest(engine="faster-whisper")
    return engine


@pytest.mark.asyncio
async def test_faster_whisper_bounds_vad_restored_tail_using_transcribe_metadata(
    monkeypatch, caplog,
) -> None:
    published = []

    async def publish(event):
        published.append(event)

    engine = native_timestamp_engine(
        monkeypatch, publish,
        info=SimpleNamespace(duration=4.0, duration_after_vad=3.15),
    )
    await engine._process_chunk(pcm(4))
    await engine._process_chunk(pcm(4))

    assert [(event.start_ms, event.end_ms) for event in published] == [
        (850, 4000), (4850, 8000),
    ]
    assert published[0].text == "source speech"
    assert "VAD removed 0.850s" in caplog.text
    assert "segment timestamps remain estimates" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "end"), [
    (-0.5, 4.85), (float("nan"), 4.85), (0.85, float("nan")),
    (0.85, float("inf")), (0.85, 24.85), (5.0, 5.85), (1.0, 0.5),
    (0.85, 5.11),
])
async def test_faster_whisper_native_invalid_timestamps_still_rejected(
    monkeypatch, start, end,
) -> None:
    published = []

    async def publish(event):
        published.append(event)

    engine = native_timestamp_engine(
        monkeypatch, publish, start=start, end=end,
        info=SimpleNamespace(duration=4.0, duration_after_vad=3.15),
    )
    with pytest.raises(AsrEngineError, match="out-of-bounds"):
        await engine._process_chunk(pcm(4))
    assert not published


@pytest.mark.asyncio
@pytest.mark.parametrize("info", [
    None,
    SimpleNamespace(duration=5.0, duration_after_vad=3.15),
    SimpleNamespace(duration=4.0, duration_after_vad=4.0),
    SimpleNamespace(duration=4.0, duration_after_vad=0.0),
    SimpleNamespace(duration=4.0, duration_after_vad=float("nan")),
    SimpleNamespace(duration=4.0, duration_after_vad=5.0),
])
async def test_faster_whisper_requires_valid_vad_metadata_to_bound_tail(monkeypatch, info):
    published = []

    async def publish(event):
        published.append(event)

    engine = native_timestamp_engine(monkeypatch, publish, info=info)
    with pytest.raises(AsrEngineError, match="out-of-bounds"):
        await engine._process_chunk(pcm(4))
    assert not published
