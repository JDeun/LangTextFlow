from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .benchmark import (
    MemoryTracker,
    inspect_wav,
    iter_f32le_wav_chunks,
    percentile,
    quality_metrics,
)
from .config import get_settings
from .models import CaptionStage, ProductPreset, SessionContext, StartSessionRequest, TranscriptEvent
from .runtime import CaptionRuntime
from .telemetry import RealtimeMetrics


@dataclass(frozen=True)
class RuntimeBenchmarkOptions:
    audio_path: Path
    engine: str
    source_language: str
    target_languages: tuple[str, ...]
    translation_provider: str
    translation_model: str | None
    preset: ProductPreset
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
class RuntimeMetricsCollector:
    asr_latency_ms: list[float] = field(default_factory=list)
    correction_latency_ms: list[float] = field(default_factory=list)
    translation_latency_ms: list[float] = field(default_factory=list)
    commit_latency_ms: list[float] = field(default_factory=list)
    postprocess_queue_peak: int = 0
    persistence_queue_peak: int = 0
    failover_audio_ms: float | None = None
    _last_failover_count: int = 0
    _last_values: dict[str, float | None] = field(default_factory=dict)

    def sample(self, metrics: RealtimeMetrics) -> None:
        self.postprocess_queue_peak = max(
            self.postprocess_queue_peak,
            metrics.postprocess_queue_depth,
        )
        self.persistence_queue_peak = max(
            self.persistence_queue_peak,
            metrics.persistence_queue_depth,
        )
        if metrics.asr_failover_count > self._last_failover_count:
            self.failover_audio_ms = metrics.audio_duration_ms
            self._last_failover_count = metrics.asr_failover_count
        self._append_changed("asr", metrics.last_asr_lag_ms, self.asr_latency_ms)
        self._append_changed(
            "correction",
            metrics.last_correction_latency_ms,
            self.correction_latency_ms,
        )
        self._append_changed(
            "translation",
            metrics.last_translation_latency_ms,
            self.translation_latency_ms,
        )
        self._append_changed("commit", metrics.last_commit_latency_ms, self.commit_latency_ms)

    def _append_changed(self, key: str, value: float | None, output: list[float]) -> None:
        if value is None:
            self._last_values[key] = None
            return
        if self._last_values.get(key) != value:
            output.append(float(value))
            self._last_values[key] = float(value)


class InstrumentedRuntime(CaptionRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.observed_events: list[TranscriptEvent] = []
        super().__init__(*args, **kwargs)

    async def _publish(self, event: TranscriptEvent) -> None:
        self.observed_events.append(event.model_copy(deep=True))
        await super()._publish(event)


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values), 1),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "max_ms": round(max(values), 1),
    }


def max_timeline_gap_ms(events: list[TranscriptEvent]) -> int | None:
    timed = sorted(
        (event for event in events if event.end_ms is not None),
        key=lambda event: (event.start_ms, int(event.end_ms or event.start_ms)),
    )
    if len(timed) < 2:
        return None
    return max(
        0,
        max(
            current.start_ms - int(previous.end_ms or previous.start_ms)
            for previous, current in zip(timed, timed[1:], strict=False)
        ),
    )


def failover_caption_gap_ms(
    events: list[TranscriptEvent],
    failover_audio_ms: float | None,
) -> int | None:
    if failover_audio_ms is None:
        return None
    point = int(round(failover_audio_ms))
    timed = [event for event in events if event.end_ms is not None]
    if any(event.start_ms <= point <= int(event.end_ms or point) for event in timed):
        return 0
    previous_ends = [int(event.end_ms or 0) for event in timed if int(event.end_ms or 0) <= point]
    next_starts = [event.start_ms for event in timed if event.start_ms >= point]
    if not previous_ends or not next_starts:
        return None
    return max(0, min(next_starts) - max(previous_ends))


def duplicate_metrics(events: list[TranscriptEvent]) -> dict[str, float | int]:
    normalized = [" ".join(event.text.casefold().split()) for event in events if event.text.strip()]
    if not normalized:
        return {"exact_duplicate_count": 0, "exact_duplicate_rate": 0.0}
    seen: set[str] = set()
    duplicates = 0
    for text in normalized:
        if text in seen:
            duplicates += 1
        else:
            seen.add(text)
    return {
        "exact_duplicate_count": duplicates,
        "exact_duplicate_rate": round(duplicates / len(normalized), 4),
    }


async def _sleep_until(target: float) -> None:
    delay = target - time.perf_counter()
    if delay > 0:
        await asyncio.sleep(delay)


async def _wait_for_runtime_quiet(
    runtime: CaptionRuntime,
    collector: RuntimeMetricsCollector,
    memory: MemoryTracker,
    *,
    settle_seconds: float,
    timeout_seconds: float,
) -> tuple[float, bool]:
    started = time.perf_counter()
    deadline = started + timeout_seconds
    last_event_at = None
    quiet_since = time.perf_counter()
    while time.perf_counter() < deadline:
        await asyncio.sleep(0.1)
        metrics = runtime.metrics_snapshot()
        collector.sample(metrics)
        memory.sample()
        queues_empty = (
            metrics.asr_queue_depth == 0
            and metrics.postprocess_queue_depth == 0
            and metrics.persistence_queue_depth == 0
        )
        if metrics.last_event_at != last_event_at:
            last_event_at = metrics.last_event_at
            quiet_since = time.perf_counter()
            continue
        if queues_empty and time.perf_counter() - quiet_since >= settle_seconds:
            return (time.perf_counter() - started, True)
    return (time.perf_counter() - started, False)


async def run_runtime_benchmark(options: RuntimeBenchmarkOptions) -> dict[str, Any]:
    info = inspect_wav(options.audio_path)
    requested_seconds = (
        options.duration_minutes * 60.0
        if options.duration_minutes is not None
        else info.duration_seconds
    )
    requested_frames = max(1, math.ceil(requested_seconds * info.sample_rate))
    memory = MemoryTracker()
    memory.start()
    metrics_collector = RuntimeMetricsCollector()

    with tempfile.TemporaryDirectory(prefix="langtextflow-runtime-benchmark-") as temp_dir:
        settings = get_settings().model_copy(
            update={"database_path": str(Path(temp_dir) / "benchmark.db")}
        )
        runtime = InstrumentedRuntime(settings=settings)
        request = StartSessionRequest(
            source_language=options.source_language,
            target_languages=list(options.target_languages),
            engine=options.engine,
            translation_provider=options.translation_provider,
            translation_model=options.translation_model,
            context=SessionContext(
                title=options.title,
                preset=options.preset,
                hotwords=list(options.hotwords),
                audience_access=False,
            ),
        )

        startup_started = time.perf_counter()
        await runtime.start(request)
        startup_seconds = time.perf_counter() - startup_started
        if runtime.engine is None:
            await runtime.shutdown()
            raise RuntimeError("runtime benchmark ASR engine did not start")
        if runtime.engine.sample_rate != info.sample_rate:
            await runtime.shutdown()
            raise ValueError(
                f"WAV sample rate {info.sample_rate} Hz does not match ASR input "
                f"{runtime.engine.sample_rate} Hz"
            )

        chunk_frames = max(1, round(info.sample_rate * options.chunk_ms / 1000))
        fed_frames = 0
        source_loops = 0
        audio_started = time.perf_counter()
        feed_finished_at = audio_started
        drain_seconds = 0.0
        drained = False
        final_metrics = runtime.metrics_snapshot()
        final_provider = runtime.state.engine
        persistence_error = runtime.state.persistence_error

        try:
            while fed_frames < requested_frames:
                source_loops += 1
                for pcm_f32le in iter_f32le_wav_chunks(
                    options.audio_path,
                    chunk_frames=chunk_frames,
                ):
                    remaining = requested_frames - fed_frames
                    frame_count = len(pcm_f32le) // 4
                    if frame_count > remaining:
                        pcm_f32le = pcm_f32le[: remaining * 4]
                        frame_count = remaining
                    if frame_count <= 0:
                        break

                    fed_frames += frame_count
                    if options.pace == "realtime":
                        await _sleep_until(audio_started + fed_frames / info.sample_rate)
                    await runtime.feed_audio(pcm_f32le)
                    metrics_collector.sample(runtime.metrics_snapshot())
                    memory.sample()
                    if fed_frames >= requested_frames:
                        break

            feed_finished_at = time.perf_counter()
            await runtime.end_audio()
            drain_seconds, drained = await _wait_for_runtime_quiet(
                runtime,
                metrics_collector,
                memory,
                settle_seconds=options.settle_seconds,
                timeout_seconds=options.drain_timeout_seconds,
            )
            final_metrics = runtime.metrics_snapshot()
            metrics_collector.sample(final_metrics)
            final_provider = runtime.state.engine
            persistence_error = runtime.state.persistence_error
            memory.sample(force=True)
        finally:
            await runtime.shutdown()

        finished_at = time.perf_counter()
        events = list(runtime.observed_events)

    stable = [event for event in events if event.stage is CaptionStage.STABLE]
    committed = [event for event in events if event.stage is CaptionStage.COMMITTED]
    hypothesis = " ".join(event.text for event in committed if event.text).strip()
    reference = (
        options.reference_path.read_text(encoding="utf-8").strip()
        if options.reference_path is not None
        else None
    )
    audio_duration_seconds = fed_frames / info.sample_rate
    feed_wall_seconds = feed_finished_at - audio_started
    total_audio_phase_seconds = finished_at - audio_started
    memory_report = memory.report()

    status = "failed" if final_metrics.asr_failure else "ok"
    if not drained and status == "ok":
        status = "drain-timeout"

    return {
        "schema_version": 1,
        "scope": "runtime",
        "status": status,
        "config": {
            "requested_engine": options.engine,
            "resolved_provider": final_provider,
            "source_language": options.source_language,
            "target_languages": list(options.target_languages),
            "translation_provider": options.translation_provider,
            "translation_model": options.translation_model,
            "preset": options.preset.value,
            "pace": options.pace,
            "chunk_ms": options.chunk_ms,
            "duration_minutes": options.duration_minutes,
            "hotwords": list(options.hotwords),
        },
        "input": {
            "path": str(options.audio_path),
            "sample_rate": info.sample_rate,
            "source_duration_ms": round(info.duration_seconds * 1000.0, 1),
            "benchmark_duration_ms": round(audio_duration_seconds * 1000.0, 1),
            "source_loops": source_loops,
            "reference_path": str(options.reference_path) if options.reference_path else None,
        },
        "timing": {
            "startup_ms": round(startup_seconds * 1000.0, 1),
            "feed_wall_ms": round(feed_wall_seconds * 1000.0, 1),
            "drain_ms": round(drain_seconds * 1000.0, 1),
            "total_audio_phase_ms": round(total_audio_phase_seconds * 1000.0, 1),
            "wall_to_audio_ratio": round(total_audio_phase_seconds / audio_duration_seconds, 4),
            "throughput_rtf": round(total_audio_phase_seconds / audio_duration_seconds, 4)
            if options.pace == "max"
            else None,
            "drained_before_timeout": drained,
            "asr": latency_summary(metrics_collector.asr_latency_ms),
            "correction": latency_summary(metrics_collector.correction_latency_ms),
            "translation": latency_summary(metrics_collector.translation_latency_ms),
            "commit": latency_summary(metrics_collector.commit_latency_ms),
        },
        "queues": {
            "asr_high_watermark": final_metrics.asr_queue_high_watermark,
            "postprocess_high_watermark": metrics_collector.postprocess_queue_peak,
            "persistence_high_watermark": metrics_collector.persistence_queue_peak,
            "audio_backpressure_events": final_metrics.audio_backpressure_events,
        },
        "failover": {
            "count": final_metrics.asr_failover_count,
            "last_reason": final_metrics.asr_last_failover_reason,
            "audio_ms": metrics_collector.failover_audio_ms,
            "caption_gap_ms": failover_caption_gap_ms(
                committed,
                metrics_collector.failover_audio_ms,
            ),
        },
        "memory": memory_report,
        "quality": {
            **quality_metrics(reference, hypothesis),
            **duplicate_metrics(committed),
            "stable_segment_count": len(stable),
            "committed_segment_count": len(committed),
            "max_timeline_gap_ms": max_timeline_gap_ms(committed),
            "hypothesis_characters": len(hypothesis),
            "reference_characters": len(reference) if reference is not None else None,
        },
        "persistence_error": persistence_error,
        "transcript": hypothesis,
    }


def _parse_csv_items(values: list[str]) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        for item in value.split(","):
            normalized = item.strip()
            if normalized and normalized not in output:
                output.append(normalized)
    return tuple(output)


def parse_args(argv: list[str] | None = None) -> RuntimeBenchmarkOptions:
    parser = argparse.ArgumentParser(description="Benchmark the full LangTextFlow runtime pipeline")
    parser.add_argument("audio", type=Path, help="16 kHz mono PCM16 WAV fixture")
    parser.add_argument(
        "--engine",
        choices=["auto", "vibevoice", "faster-whisper"],
        default="auto",
    )
    parser.add_argument("--source-language", default="ko")
    parser.add_argument("--target-language", action="append", default=[])
    parser.add_argument(
        "--translation-provider",
        choices=["none", "demo", "ollama"],
        default="none",
    )
    parser.add_argument("--translation-model")
    parser.add_argument(
        "--preset",
        choices=[preset.value for preset in ProductPreset],
        default=ProductPreset.GENERAL.value,
    )
    parser.add_argument("--pace", choices=["realtime", "max"], default="realtime")
    parser.add_argument("--chunk-ms", type=int, default=100)
    parser.add_argument("--duration-minutes", type=float)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--drain-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--title", default="LangTextFlow runtime benchmark")
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

    targets = _parse_csv_items(args.target_language) or ("en",)
    return RuntimeBenchmarkOptions(
        audio_path=args.audio,
        engine=args.engine,
        source_language=args.source_language,
        target_languages=targets,
        translation_provider=args.translation_provider,
        translation_model=args.translation_model,
        preset=ProductPreset(args.preset),
        pace=args.pace,
        chunk_ms=args.chunk_ms,
        duration_minutes=args.duration_minutes,
        settle_seconds=args.settle_seconds,
        drain_timeout_seconds=args.drain_timeout_seconds,
        reference_path=args.reference,
        output_path=args.output,
        title=args.title,
        hotwords=_parse_csv_items(args.hotword),
    )


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        report = asyncio.run(run_runtime_benchmark(options))
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
