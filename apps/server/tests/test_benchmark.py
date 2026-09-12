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

    metrics = quality_metrics("오늘 우리는 요한복음 3장을 봅니다", "오늘 우리는 요한복음 3장을 봅니다")
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
