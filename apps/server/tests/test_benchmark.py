from __future__ import annotations

import wave
from array import array

import pytest

from langtextflow.benchmark import (
    ObservedSegment,
    error_rate,
    inspect_wav,
    iter_f32le_wav_chunks,
    normalize_text,
    percentile,
    quality_metrics,
    segment_quality,
)


def write_pcm16_wav(path, *, sample_rate: int = 16000, channels: int = 1) -> None:
    samples = array("h", [-32768, 0, 16384, 32767])
    if channels == 2:
        stereo = array("h")
        for sample in samples:
            stereo.extend([sample, sample])
        samples = stereo
    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(samples.tobytes())


def test_wav_reader_converts_pcm16_to_float32(tmp_path) -> None:
    path = tmp_path / "fixture.wav"
    write_pcm16_wav(path)

    info = inspect_wav(path)
    chunks = list(iter_f32le_wav_chunks(path, chunk_frames=2))
    first = array("f")
    first.frombytes(chunks[0])
    second = array("f")
    second.frombytes(chunks[1])

    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.sample_width == 2
    assert info.frame_count == 4
    assert len(chunks) == 2
    assert list(first) == pytest.approx([-1.0, 0.0])
    assert list(second) == pytest.approx([0.5, 32767 / 32768])


def test_wav_reader_rejects_stereo_fixture(tmp_path) -> None:
    path = tmp_path / "stereo.wav"
    write_pcm16_wav(path, channels=2)

    with pytest.raises(ValueError, match="mono 16-bit PCM"):
        inspect_wav(path)


def test_text_quality_metrics_are_deterministic() -> None:
    assert normalize_text("  요한복음\n3장  ") == "요한복음 3장"
    assert error_rate(["a", "b", "c"], ["a", "x", "c"]) == pytest.approx(1 / 3)

    sentence = "오늘 우리는 요한복음 3장을 봅니다"
    metrics = quality_metrics(sentence, sentence)
    assert metrics == {"cer": 0.0, "wer": 0.0}

    changed = quality_metrics("John chapter three", "John chapter four")
    assert changed["wer"] == pytest.approx(1 / 3, abs=0.0001)
    assert changed["cer"] is not None
    assert changed["cer"] > 0


def test_percentile_uses_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.95) == 100.0
    assert percentile([], 0.95) is None


def test_segment_quality_flags_exact_duplicates_and_timestamp_overlap() -> None:
    segments = [
        ObservedSegment("a", "hello", 0, 1000, 1200.0, 200.0),
        ObservedSegment("b", "hello", 900, 1500, 1700.0, 200.0),
        ObservedSegment("c", "world", 1500, 2000, 2200.0, 200.0),
    ]

    metrics = segment_quality(segments)

    assert metrics["exact_adjacent_duplicate_count"] == 1
    assert metrics["exact_adjacent_duplicate_rate"] == pytest.approx(1 / 3, abs=0.0001)
    assert metrics["timestamp_overlap_candidate_count"] == 1
    assert metrics["timestamp_overlap_candidate_rate"] == pytest.approx(1 / 3, abs=0.0001)


def test_streaming_revisions_do_not_inflate_asr_quality() -> None:
    """Regression for #101: duplicated or stale hypotheses must not raise CER."""
    import asyncio

    from langtextflow.benchmark import SegmentCollector
    from langtextflow.models import CaptionStage, TranscriptEvent

    collector = SegmentCollector(pace="max")
    collector.start_clock()

    def event(segment: str, version: int, stage: CaptionStage, text: str,
              *, start: int = 0, end: int = 1000) -> TranscriptEvent:
        return TranscriptEvent(
            segment_id=segment, version=version, stage=stage,
            source_language="ko", text=text, start_ms=start, end_ms=end,
            committed=stage is CaptionStage.COMMITTED,
        )

    async def replay() -> None:
        await collector.publish(event("s1", 1, CaptionStage.PARTIAL, "틀린 가설"))
        await collector.publish(event("s1", 1, CaptionStage.STABLE, "예수님"))
        await collector.publish(event("s1", 1, CaptionStage.STABLE, "예수님"))
        await collector.publish(event("s1", 2, CaptionStage.STABLE, "주 예수님"))
        await collector.publish(event("s1", 1, CaptionStage.STABLE, "오래된 가설"))
        await collector.publish(event("s1", 2, CaptionStage.COMMITTED, "주 예수님"))
        await collector.publish(event("s1", 3, CaptionStage.STABLE, "뒤늦은 수정"))
        await collector.publish(event(
            "s2", 1, CaptionStage.STABLE, "은혜", start=1000, end=2000
        ))
        await collector.publish(event(
            "s2", 2, CaptionStage.COMMITTED, "은혜", start=1000, end=2000
        ))

    asyncio.run(replay())
    assert len(collector.segments) == 2
    assert [row.segment_id for row in collector.segments] == ["s1", "s2"]
    assert collector.first_stable_elapsed_ms is not None
    assert collector.first_stable_elapsed_ms <= collector.segments[0].received_elapsed_ms
    hypothesis = " ".join(row.text for row in collector.segments)
    assert hypothesis == "주 예수님 은혜"
    assert quality_metrics(hypothesis, hypothesis) == {"cer": 0.0, "wer": 0.0}


def test_distinct_equal_text_segments_remain_visible_for_diagnostics() -> None:
    """Never discard two separately identified segments by text equality."""
    import asyncio

    from langtextflow.benchmark import SegmentCollector
    from langtextflow.models import CaptionStage, TranscriptEvent

    collector = SegmentCollector(pace="max")
    collector.start_clock()

    async def replay() -> None:
        for identifier, start in (("one", 0), ("two", 1000)):
            await collector.publish(TranscriptEvent(
                segment_id=identifier, version=1, stage=CaptionStage.STABLE,
                source_language="en", text="amen",
                start_ms=start, end_ms=start + 1000,
            ))

    asyncio.run(replay())
    assert [row.text for row in collector.segments] == ["amen", "amen"]
    assert segment_quality(collector.segments)["exact_adjacent_duplicate_count"] == 1
