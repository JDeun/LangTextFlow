from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import sys
import time
import unicodedata
import wave
from array import array
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .asr import (
    AsrEngine,
    FasterWhisperStreamingAsrEngine,
    ReplayFallbackAsrEngine,
    VibeVoiceStreamingAsrEngine,
)
from .asr.base import PublishEvent
from .config import Settings, get_settings
from .models import CaptionStage, SessionContext, StartSessionRequest, TranscriptEvent


@dataclass(frozen=True)
class WavInfo:
    path: Path
    sample_rate: int
    channels: int
    sample_width: int
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.sample_rate


@dataclass(frozen=True)
class BenchmarkOptions:
    audio_path: Path
    engine: str
    source_language: str
    pace: str
    chunk_ms: int
    duration_minutes: float | None
    settle_seconds: float
    drain_timeout_seconds: float
    reference_path: Path | None
    output_path: Path | None
    title: str
    hotwords: tuple[str, ...]


@dataclass
class ObservedSegment:
    segment_id: str
    text: str
    start_ms: int
    end_ms: int | None
    received_elapsed_ms: float
    realtime_lag_ms: float | None


class SegmentCollector:
    def __init__(self, *, pace: str) -> None:
        self.pace = pace
        self.started_at: float | None = None
        self.segments: list[ObservedSegment] = []

    def start_clock(self) -> None:
        self.started_at = time.perf_counter()

    async def publish(self, event: TranscriptEvent) -> None:
        if event.stage is not CaptionStage.STABLE:
            return
        if self.started_at is None:
            raise RuntimeError("benchmark collector clock has not started")
        elapsed_ms = (time.perf_counter() - self.started_at) * 1000.0
        lag_ms: float | None = None
        if self.pace == "realtime" and event.end_ms is not None:
            lag_ms = max(0.0, elapsed_ms - event.end_ms)
        self.segments.append(
            ObservedSegment(
                segment_id=event.segment_id,
                text=event.text.strip(),
                start_ms=event.start_ms,
                end_ms=event.end_ms,
                received_elapsed_ms=round(elapsed_ms, 1),
                realtime_lag_ms=round(lag_ms, 1) if lag_ms is not None else None,
            )
        )


class MemoryTracker:
    """Optional RSS sampler. psutil is intentionally a benchmark-only dependency."""

    def __init__(self, *, interval_seconds: float = 5.0) -> None:
        self.interval_seconds = max(0.1, interval_seconds)
        self.samples: list[dict[str, float]] = []
        self.available = False
        self._process: Any | None = None
        self._started_at: float | None = None
        self._last_sample_at = 0.0
        try:
            import psutil

            self._process = psutil.Process()
            self.available = True
        except ImportError:
            self._process = None

    def start(self) -> None:
        self._started_at = time.perf_counter()
        self._last_sample_at = 0.0
        self.sample(force=True)

    def sample(self, *, force: bool = False) -> None:
        if not self.available or self._process is None or self._started_at is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_sample_at < self.interval_seconds:
            return
        rss_mb = self._process.memory_info().rss / (1024 * 1024)
        self.samples.append(
            {
                "elapsed_seconds": round(now - self._started_at, 3),
                "rss_mb": round(rss_mb, 2),
            }
        )
        self._last_sample_at = now

    def report(self) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "rss_peak_mb": None,
                "rss_growth_mb": None,
                "samples": [],
            }
        values = [sample["rss_mb"] for sample in self.samples]
        return {
            "available": True,
            "rss_peak_mb": max(values) if values else None,
            "rss_growth_mb": round(values[-1] - values[0], 2) if len(values) >= 2 else 0.0,
            "samples": self.samples,
        }


def inspect_wav(path: Path) -> WavInfo:
    with wave.open(str(path), "rb") as source:
        info = WavInfo(
            path=path,
            sample_rate=source.getframerate(),
            channels=source.getnchannels(),
            sample_width=source.getsampwidth(),
            frame_count=source.getnframes(),
        )
    if info.channels != 1 or info.sample_width != 2:
        raise ValueError(
            "benchmark WAV must be mono 16-bit PCM; preprocess with ffmpeg to 16 kHz mono PCM16"
        )
    return info


def pcm16le_to_f32le(raw: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    floats = array("f", (sample / 32768.0 for sample in samples))
    if sys.byteorder != "little":
        floats.byteswap()
    return floats.tobytes()


def iter_f32le_wav_chunks(path: Path, *, chunk_frames: int) -> Iterator[bytes]:
    if chunk_frames <= 0:
        raise ValueError("chunk_frames must be greater than zero")
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("benchmark WAV must be mono 16-bit PCM")
        while True:
            raw = source.readframes(chunk_frames)
            if not raw:
                return
            yield pcm16le_to_f32le(raw)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def levenshtein_distance(reference: list[str], hypothesis: list[str]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            insertion = current[column - 1] + 1
            deletion = previous[column] + 1
            substitution = previous[column - 1] + (reference_item != hypothesis_item)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def error_rate(reference: list[str], hypothesis: list[str]) -> float | None:
    if not reference:
        return None
    return levenshtein_distance(reference, hypothesis) / len(reference)


def quality_metrics(reference: str | None, hypothesis: str) -> dict[str, Any]:
    if reference is None:
        return {"cer": None, "wer": None}
    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    ref_chars = list(ref.replace(" ", ""))
    hyp_chars = list(hyp.replace(" ", ""))
    return {
        "cer": _round_optional(error_rate(ref_chars, hyp_chars), 4),
        "wer": _round_optional(error_rate(ref.split(), hyp.split()), 4),
    }


def _round_optional(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return round(ordered[rank], 1)


def segment_quality(segments: list[ObservedSegment]) -> dict[str, Any]:
    exact_adjacent_duplicates = 0
    timestamp_overlap_candidates = 0
    previous: ObservedSegment | None = None
    for segment in segments:
        if previous is not None:
            if normalize_text(previous.text) == normalize_text(segment.text) and segment.text:
                exact_adjacent_duplicates += 1
            if previous.end_ms is not None and segment.start_ms < previous.end_ms:
                timestamp_overlap_candidates += 1
        previous = segment
    count = len(segments)
    return {
        "exact_adjacent_duplicate_count": exact_adjacent_duplicates,
        "exact_adjacent_duplicate_rate": round(exact_adjacent_duplicates / count, 4)
        if count
        else 0.0,
        "timestamp_overlap_candidate_count": timestamp_overlap_candidates,
        "timestamp_overlap_candidate_rate": round(timestamp_overlap_candidates / count, 4)
        if count
        else 0.0,
    }


def build_asr_engine(
    engine_name: str,
    publish: PublishEvent,
    settings: Settings,
) -> AsrEngine:
    def vibevoice(provider_publish: PublishEvent) -> VibeVoiceStreamingAsrEngine:
        return VibeVoiceStreamingAsrEngine(
            provider_publish,
            base_url=settings.vibevoice_url,
            queue_chunks=settings.audio_queue_chunks,
            max_frame_bytes=settings.max_audio_frame_bytes,
        )

    def faster_whisper(provider_publish: PublishEvent) -> FasterWhisperStreamingAsrEngine:
        return FasterWhisperStreamingAsrEngine(
            provider_publish,
            model=settings.faster_whisper_model,
            device=settings.faster_whisper_device,
            compute_type=settings.faster_whisper_compute_type,
            chunk_seconds=settings.faster_whisper_chunk_seconds,
            queue_chunks=settings.audio_queue_chunks,
            max_frame_bytes=settings.max_audio_frame_bytes,
        )

    if engine_name == "vibevoice":
        return vibevoice(publish)
    if engine_name == "faster-whisper":
        return faster_whisper(publish)
    if engine_name == "auto":
        return ReplayFallbackAsrEngine(
            publish,
            [("vibevoice", vibevoice), ("faster-whisper", faster_whisper)],
            replay_seconds=settings.asr_replay_seconds,
            health_check_seconds=settings.asr_health_check_seconds,
        )
    raise ValueError("benchmark engine must be auto, vibevoice, or faster-whisper")


async def _sleep_until(target: float) -> None:
    delay = target - time.perf_counter()
    if delay > 0:
        await asyncio.sleep(delay)


async def _drain_engine(
    engine: AsrEngine,
    *,
    settle_seconds: float,
    timeout_seconds: float,
    memory: MemoryTracker,
) -> tuple[float, bool]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    zero_since: float | None = None
    while time.perf_counter() < deadline:
        memory.sample()
        if engine.failure is not None:
            break
        if engine.queue_depth == 0:
            if zero_since is None:
                zero_since = time.perf_counter()
            if time.perf_counter() - zero_since >= settle_seconds:
                return (time.perf_counter() - started, True)
        else:
            zero_since = None
        await asyncio.sleep(0.05)
    return (time.perf_counter() - started, False)


async def run_benchmark(options: BenchmarkOptions) -> dict[str, Any]:
    info = inspect_wav(options.audio_path)
    settings = get_settings()
    collector = SegmentCollector(pace=options.pace)
    engine = build_asr_engine(options.engine, collector.publish, settings)
    if engine.sample_rate != info.sample_rate:
        raise ValueError(
            f"WAV sample rate {info.sample_rate} Hz does not match ASR input "
            f"{engine.sample_rate} Hz"
        )

    request = StartSessionRequest(
        source_language=options.source_language,
        target_languages=[options.source_language],
        engine=options.engine,
        translation_provider="none",
        context=SessionContext(
            title=options.title,
            hotwords=list(options.hotwords),
            audience_access=False,
        ),
    )
    memory = MemoryTracker()
    memory.start()

    startup_started = time.perf_counter()
    await engine.start(request)
    startup_seconds = time.perf_counter() - startup_started
    memory.sample(force=True)

    chunk_frames = max(1, round(info.sample_rate * options.chunk_ms / 1000))
    requested_seconds = (
        options.duration_minutes * 60.0 if options.duration_minutes is not None else info.duration_seconds
    )
    requested_frames = max(1, round(requested_seconds * info.sample_rate))
    fed_frames = 0
    source_loops = 0
    queue_high_watermark = 0
    queue_capacity = engine.queue_capacity
    collector.start_clock()
    audio_started = collector.started_at
    assert audio_started is not None

    try:
        while fed_frames < requested_frames:
            source_loops += 1
            for pcm_f32le in iter_f32le_wav_chunks(options.audio_path, chunk_frames=chunk_frames):
                remaining_frames = requested_frames - fed_frames
                frame_count = len(pcm_f32le) // 4
                if frame_count > remaining_frames:
                    pcm_f32le = pcm_f32le[: remaining_frames * 4]
                    frame_count = remaining_frames
                if frame_count <= 0:
                    break

                fed_frames += frame_count
                if options.pace == "realtime":
                    target = audio_started + fed_frames / info.sample_rate
                    await _sleep_until(target)
                await engine.feed_audio(pcm_f32le)
                queue_high_watermark = max(queue_high_watermark, engine.queue_depth)
                queue_capacity = max(queue_capacity, engine.queue_capacity)
                memory.sample()
                if fed_frames >= requested_frames:
                    break

        feed_finished_at = time.perf_counter()
        await engine.end_audio()
        drain_seconds, drained = await _drain_engine(
            engine,
            settle_seconds=options.settle_seconds,
            timeout_seconds=options.drain_timeout_seconds,
            memory=memory,
        )
    finally:
        await engine.stop()
        memory.sample(force=True)

    finished_at = time.perf_counter()
    audio_duration_seconds = fed_frames / info.sample_rate
    feed_wall_seconds = feed_finished_at - audio_started
    total_audio_phase_seconds = finished_at - audio_started
    lag_values = [
        segment.realtime_lag_ms
        for segment in collector.segments
        if segment.realtime_lag_ms is not None
    ]
    hypothesis = " ".join(segment.text for segment in collector.segments if segment.text).strip()
    reference = (
        options.reference_path.read_text(encoding="utf-8").strip()
        if options.reference_path is not None
        else None
    )
    resolved_provider = getattr(engine, "active_provider", None) or options.engine

    timing = {
        "startup_ms": round(startup_seconds * 1000.0, 1),
        "audio_duration_ms": round(audio_duration_seconds * 1000.0, 1),
        "feed_wall_ms": round(feed_wall_seconds * 1000.0, 1),
        "drain_ms": round(drain_seconds * 1000.0, 1),
        "total_audio_phase_ms": round(total_audio_phase_seconds * 1000.0, 1),
        "wall_to_audio_ratio": round(total_audio_phase_seconds / audio_duration_seconds, 4),
        "throughput_rtf": round(total_audio_phase_seconds / audio_duration_seconds, 4)
        if options.pace == "max"
        else None,
        "first_stable_ms": collector.segments[0].received_elapsed_ms if collector.segments else None,
        "realtime_lag_p50_ms": percentile(lag_values, 0.50),
        "realtime_lag_p95_ms": percentile(lag_values, 0.95),
        "realtime_lag_max_ms": round(max(lag_values), 1) if lag_values else None,
        "drained_before_timeout": drained,
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or None,
        },
        "config": {
            "requested_engine": options.engine,
            "resolved_provider": resolved_provider,
            "source_language": options.source_language,
            "pace": options.pace,
            "chunk_ms": options.chunk_ms,
            "duration_minutes": options.duration_minutes,
            "settle_seconds": options.settle_seconds,
            "drain_timeout_seconds": options.drain_timeout_seconds,
            "hotwords": list(options.hotwords),
            "faster_whisper_model": settings.faster_whisper_model,
            "faster_whisper_device": settings.faster_whisper_device,
            "faster_whisper_compute_type": settings.faster_whisper_compute_type,
            "faster_whisper_chunk_seconds": settings.faster_whisper_chunk_seconds,
            "asr_replay_seconds": settings.asr_replay_seconds,
            "asr_health_check_seconds": settings.asr_health_check_seconds,
        },
        "input": {
            "path": str(options.audio_path),
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "sample_width_bits": info.sample_width * 8,
            "source_duration_ms": round(info.duration_seconds * 1000.0, 1),
            "benchmark_duration_ms": round(audio_duration_seconds * 1000.0, 1),
            "source_loops": source_loops,
            "reference_path": str(options.reference_path) if options.reference_path else None,
        },
        "provider": {
            "failure": engine.failure,
            "failover_count": engine.failover_count,
            "last_failover_reason": engine.last_failover_reason,
            "queue_high_watermark": queue_high_watermark,
            "queue_capacity": queue_capacity,
        },
        "timing": timing,
        "memory": memory.report(),
        "quality": {
            **quality_metrics(reference, hypothesis),
            **segment_quality(collector.segments),
            "segment_count": len(collector.segments),
            "hypothesis_characters": len(hypothesis),
            "reference_characters": len(reference) if reference is not None else None,
        },
        "transcript": hypothesis,
        "segments": [
            {
                "segment_id": segment.segment_id,
                "text": segment.text,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "received_elapsed_ms": segment.received_elapsed_ms,
                "realtime_lag_ms": segment.realtime_lag_ms,
            }
            for segment in collector.segments
        ],
    }


def _parse_hotwords(values: list[str]) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        for item in value.split(","):
            normalized = item.strip()
            if normalized and normalized not in output:
                output.append(normalized)
    return tuple(output)


def parse_args(argv: list[str] | None = None) -> BenchmarkOptions:
    parser = argparse.ArgumentParser(description="Benchmark LangTextFlow streaming ASR providers")
    parser.add_argument("audio", type=Path, help="16 kHz mono PCM16 WAV fixture")
    parser.add_argument(
        "--engine",
        choices=["auto", "vibevoice", "faster-whisper"],
        default="auto",
    )
    parser.add_argument("--source-language", default="ko")
    parser.add_argument("--pace", choices=["realtime", "max"], default="realtime")
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument(
        "--duration-minutes",
        type=float,
        help="repeat the WAV until this audio duration is reached (for soak tests)",
    )
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--drain-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--title", default="LangTextFlow benchmark")
    parser.add_argument("--hotword", action="append", default=[])
    args = parser.parse_args(argv)

    if args.chunk_ms <= 0:
        parser.error("--chunk-ms must be greater than zero")
    if args.duration_minutes is not None and args.duration_minutes <= 0:
        parser.error("--duration-minutes must be greater than zero")
    if args.settle_seconds < 0:
        parser.error("--settle-seconds cannot be negative")
    if args.drain_timeout_seconds <= 0:
        parser.error("--drain-timeout-seconds must be greater than zero")
    if not args.audio.exists():
        parser.error(f"audio fixture does not exist: {args.audio}")
    if args.reference is not None and not args.reference.exists():
        parser.error(f"reference transcript does not exist: {args.reference}")

    return BenchmarkOptions(
        audio_path=args.audio,
        engine=args.engine,
        source_language=args.source_language,
        pace=args.pace,
        chunk_ms=args.chunk_ms,
        duration_minutes=args.duration_minutes,
        settle_seconds=args.settle_seconds,
        drain_timeout_seconds=args.drain_timeout_seconds,
        reference_path=args.reference,
        output_path=args.output,
        title=args.title,
        hotwords=_parse_hotwords(args.hotword),
    )


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        report = asyncio.run(run_benchmark(options))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if options.output_path is not None:
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        options.output_path.write_text(rendered + "\n", encoding="utf-8")
        print(options.output_path)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
