"""Pace pinned FLEURS speech through the actual CaptionRuntime ASR/MT/fanout.

Source transcriptions are independent ASR references. Different-language FLEURS
IDs are NOT aligned translation references; chrF remains unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import wave
from pathlib import Path
from typing import Any

from langtextflow.benchmark import MemoryTracker, pcm16le_to_f32le, percentile, quality_metrics
from langtextflow.config import Settings
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.runtime import CaptionRuntime
from langtextflow.runtime_benchmark import RuntimeMetricsCollector, latency_summary
from scripts.evaluate_fleurs_asr import (
    FLEURS_DATASET,
    FLEURS_LICENSE,
    FLEURS_REVISION,
    fleurs_test_parquet_uri,
    write_pcm_wav,
)

MAX_EVENTS = 2048


def summarize_events(
    events: list[dict[str, Any]],
    audio_seconds: float,
    wall_seconds: float,
    *,
    source_language: str = "ko",
    target_language: str = "en",
) -> dict[str, Any]:
    """Count each final segment once; never substitute source text for a target."""
    if audio_seconds <= 0:
        raise ValueError("audio_seconds must be positive")
    if source_language == target_language:
        raise ValueError("source and target must differ")
    latest: dict[str, dict[str, Any]] = {}
    for row in events:
        previous = latest.get(row["segment_id"])
        revision = (row.get("source_revision", 0), row["version"])
        if previous is None or revision >= (
            previous.get("source_revision", 0),
            previous["version"],
        ):
            latest[row["segment_id"]] = row
    committed = sorted(
        (row for row in latest.values() if row["stage"] == "committed"),
        key=lambda row: (row.get("start_ms") or 0, row["segment_id"]),
    )
    translations = [
        row
        for row in committed
        if str(row.get("translations", {}).get(target_language, "")).strip()
    ]
    invalid = {
        str(row["segment_id"])
        for row in events
        if isinstance(row.get("end_ms"), (int, float))
        and (
            row["end_ms"] < 0
            or row["end_ms"] > audio_seconds * 1000 + 250
            or not 0 <= row.get("start_ms", 0) <= row["end_ms"]
            or row["end_ms"] > row["received_ms"] + 250
        )
    }
    by_stage = {
        stage: [row for row in events if row["stage"] == stage]
        for stage in ("partial", "stable", "translated", "committed")
    }
    latencies = {
        stage: [
            max(0.0, row["received_ms"] - row["end_ms"])
            for row in rows
            if isinstance(row.get("end_ms"), (int, float)) and str(row["segment_id"]) not in invalid
        ]
        for stage, rows in by_stage.items()
    }
    states: dict[str, int] = {}
    for row in committed:
        state = row.get("translation_states", {}).get(target_language, "unreported")
        states[state] = states.get(state, 0) + 1
    stable_ids = {row["segment_id"] for row in by_stage["stable"]}
    missing_commits = stable_ids - {row["segment_id"] for row in committed}
    return {
        "stable_count": len(stable_ids),
        "committed_count": len(committed),
        "translated_committed_count": len(translations),
        "translation_coverage": round(len(translations) / len(committed), 4) if committed else 0.0,
        "target_language": target_language,
        "target_states": states,
        "uncommitted_stable_count": len(missing_commits),
        "uncommitted_stable_rate": len(missing_commits) / len(stable_ids) if stable_ids else None,
        "empty_source_count": sum(not row["text"].strip() for row in committed),
        "asr_hypothesis": " ".join(row.get("original_text") or row["text"] for row in committed),
        "caption_source_hypothesis": " ".join(row["text"] for row in committed),
        "translated_hypothesis": " ".join(
            row["translations"][target_language] for row in translations
        ),
        "p95_lag_ms": {stage: percentile(values, 0.95) for stage, values in latencies.items()},
        "first_caption_ms": min((row["received_ms"] for row in events), default=None),
        "first_commit_ms": min((row["received_ms"] for row in committed), default=None),
        "first_translation_ms": min(
            (
                row["received_ms"]
                for row in by_stage["translated"]
                if row.get("translations", {}).get(target_language)
            ),
            default=None,
        ),
        "processing_rtf": round(wall_seconds / audio_seconds, 4),
        "translation_backlog_warning": bool(missing_commits)
        or not committed
        or len(translations) != len(committed),
        "invalid_timestamp_segment_ids": sorted(invalid),
        "invalid_timestamp_count": len(invalid),
        "field_ready": False,
    }


def load_pinned_speech(
    count: int,
    output: Path,
    source_language: str = "ko",
) -> tuple[bytes, list[dict[str, Any]], str]:
    from datasets import Audio, load_dataset

    if not 1 <= count <= 3:
        raise ValueError("sample_count must be between 1 and 3")
    dataset = load_dataset(
        "parquet",
        data_files={"test": fleurs_test_parquet_uri(source_language)},
        split="test",
        streaming=True,
    ).cast_column("audio", Audio(decode=False))
    output.mkdir(parents=True, exist_ok=True)
    pcm_bytes = bytearray()
    metadata: list[dict[str, Any]] = []
    sentences: list[str] = []
    for attempt, sample in enumerate(dataset):
        if len(metadata) == count:
            break
        if attempt >= 40:
            raise RuntimeError("insufficient eligible pinned FLEURS clips")
        sentence = str(sample.get("raw_transcription") or sample.get("transcription") or "").strip()
        if not sentence:
            continue
        wav_path = output / f"temporary-fleurs-{len(metadata)}.wav"
        try:
            seconds, digest = write_pcm_wav(sample["audio"], wav_path)
            with wave.open(str(wav_path), "rb") as wav:
                frames = wav.readframes(wav.getnframes())
            if len(frames) % 2:
                raise ValueError("corrupt PCM16 fixture")
        except (ValueError, TypeError):
            continue
        finally:
            wav_path.unlink(missing_ok=True)
        if metadata:
            pcm_bytes.extend(b"\x00" * 16_000)
        pcm_bytes.extend(frames)
        sentences.append(sentence)
        metadata.append(
            {
                "id": sample.get("id"),
                "source_language": source_language,
                "duration_seconds": seconds,
                "sha256_pcm16": digest,
                "reference": sentence,
            }
        )
    if len(metadata) != count:
        raise RuntimeError(f"insufficient FLEURS speech samples: {len(metadata)}/{count}")
    return bytes(pcm_bytes), metadata, " ".join(sentences)


class AudienceCollector:
    """Observe serialized hub delivery, not network transport or browser paint."""

    def __init__(self, session_id: str, source: str, target: str) -> None:
        self.session_id, self.source, self.target = session_id, source, target
        self.started_at = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self.overflow = 0
        self.snapshots = 0
        self.scope_errors = 0
        self.closed = False

    async def accept(self) -> None:
        pass

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        if payload.get("type") == "snapshot":
            self.snapshots += 1
            return
        if payload.get("type") != "transcript" or payload["source_language"] != self.source:
            self.scope_errors += 1
            raise ValueError("unexpected audience payload/source scope")
        if len(self.events) >= MAX_EVENTS:
            self.overflow += 1
            raise RuntimeError("benchmark audience event budget exceeded")
        self.events.append(
            {
                **payload,
                "session_id": self.session_id,
                "selected_language": self.target,
                "selected_caption": payload.get("translations", {}).get(self.target),
                "selected_target_state": payload.get("translation_states", {}).get(self.target),
                "received_ms": round((time.perf_counter() - self.started_at) * 1000, 1),
            }
        )


def model_hashes(directory: Path | None) -> dict[str, str]:
    if directory is None:
        return {}
    result = {}
    for name in (
        "model.bin",
        "config.json",
        "preprocessor_config.json",
        "vocabulary.txt",
        "vocabulary.json",
        "tokenizer.json",
        "shared_vocabulary.json",
        "tokenizer_config.json",
        "sentencepiece.bpe.model",
        "vocab.json",
    ):
        path = directory / name
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        result[name] = digest.hexdigest()
    return result


def write_report(output: Path, report: dict[str, Any]) -> None:
    """Always leave a bounded diagnostic if aggregation/serialization fails."""
    try:
        output.mkdir(parents=True, exist_ok=True)
        data = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        (output / "live-interpretation-report.json").write_text(data, encoding="utf-8")
    except Exception as exc:
        fallback = {
            "schema_version": 2,
            "status": "report-error",
            "error": str(exc)[:1000],
            "field_ready": False,
        }
        try:
            (output / "failure-report.json").write_text(
                json.dumps(fallback) + "\n", encoding="utf-8"
            )
        finally:
            print(json.dumps(fallback))
        raise


async def run_live(
    audio_pcm16: bytes,
    reference: str,
    clips: list[dict[str, Any]],
    output: Path,
    *,
    model: str,
    chunk_seconds: float,
    timeout_seconds: float,
    source_language: str = "ko",
    translation_provider: str = "ollama",
    asr_model_dir: Path | None = None,
    ct2_dir: Path | None = None,
    tokenizer_dir: Path | None = None,
    model_manifest: Path | None = None,
    measurement_mode: str = "real-inference",
) -> dict[str, Any]:
    if not audio_pcm16 or len(audio_pcm16) % 2:
        raise ValueError("expected non-empty 16-bit mono PCM audio")
    if model not in {"tiny", "base", "small"} or chunk_seconds not in {4.0, 8.0, 15.0}:
        raise ValueError("unsupported controlled ASR comparison configuration")
    if source_language not in {"ko", "en"} or translation_provider not in {"ollama", "m2m100-ct2"}:
        raise ValueError("unsupported live benchmark language/provider")
    if not 0 < timeout_seconds <= 600 or len(audio_pcm16) > 61 * 16000 * 2:
        raise ValueError("benchmark duration/deadline outside bounded pilot")
    if measurement_mode not in {"real-inference", "mock-wiring"}:
        raise ValueError("unknown measurement mode")
    target_language = "en" if source_language == "ko" else "ko"
    output.mkdir(parents=True, exist_ok=True)
    audio_seconds = len(audio_pcm16) / 32000
    local_paths = {}
    if ct2_dir is not None:
        local_paths["local_m2m100_ct2_dir"] = str(ct2_dir)
    if tokenizer_dir is not None:
        local_paths["local_m2m100_tokenizer_dir"] = str(tokenizer_dir)
    settings = Settings(
        _env_file=None,
        database_path=str(output / "live-benchmark.sqlite"),
        faster_whisper_model=str(asr_model_dir) if asr_model_dir else model,
        faster_whisper_device="cpu",
        faster_whisper_compute_type="int8",
        faster_whisper_chunk_seconds=chunk_seconds,
        audio_queue_chunks=16,
        max_segments=256,
        postprocess_drain_timeout_seconds=30.0,
        ollama_translation_timeout_seconds=180.0,
        local_m2m100_cpu_threads=2,
        **local_paths,
    )
    runtime = CaptionRuntime(settings)
    collector: AudienceCollector | None = None
    samples = RuntimeMetricsCollector()
    memory = MemoryTracker(interval_seconds=2.0)
    memory.start()
    memory_task: asyncio.Task | None = None
    cold_start_seconds = None
    failure: str | None = None
    telemetry: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    asr_diagnostics: dict[str, Any] = {}
    shutdown_telemetry: dict[str, Any] = {}
    drained = False
    drain_started_at: float | None = None
    drain_seconds: float | None = None
    startup = time.perf_counter()

    async def sample_resources() -> None:
        while True:
            memory.sample()
            samples.sample(runtime.metrics_snapshot())
            await asyncio.sleep(0.25)

    try:
        if model_manifest:
            if model_manifest.stat().st_size > 65536:
                raise ValueError("model provenance manifest too large")
            provenance = json.loads(model_manifest.read_text(encoding="utf-8"))
        provenance["asr_files_sha256"] = model_hashes(asr_model_dir)
        provenance["translation_files_sha256"] = model_hashes(
            Path(settings.local_m2m100_ct2_dir) if translation_provider == "m2m100-ct2" else None
        )
        provenance["tokenizer_files_sha256"] = model_hashes(
            Path(settings.local_m2m100_tokenizer_dir)
            if translation_provider == "m2m100-ct2"
            else None
        )
        memory_task = asyncio.create_task(sample_resources(), name="live-benchmark-resources")
        request = StartSessionRequest(
            source_language=source_language,
            target_languages=[target_language],
            engine="faster-whisper",
            translation_provider=translation_provider,
            translation_model=settings.ollama_translation_model
            if translation_provider == "ollama"
            else None,
            correction_provider="none",
            context=SessionContext(title="Licensed read-speech pilot", audience_access=False),
        )
        await asyncio.wait_for(runtime.start(request), timeout_seconds)
        cold_start_seconds = round(time.perf_counter() - startup, 3)
        if not runtime.pipeline.status.available:
            raise RuntimeError(f"translation unavailable: {runtime.pipeline.status.error}")
        collector = AudienceCollector(
            runtime.state.session_id or "", source_language, target_language
        )
        admitted = await runtime.hub.connect(
            collector,
            initial_snapshot=lambda: {
                "type": "snapshot",
                "segments": [e.model_dump(mode="json") for e in runtime.store.snapshot()],
            },
        )
        if not admitted:
            raise RuntimeError("audience observer admission rejected")
        # A single deadline bounds feeding/backpressure and every drain stage.
        async with asyncio.timeout(audio_seconds + timeout_seconds):
            sent_frames = 0
            for offset in range(0, len(audio_pcm16), 3200):
                chunk = audio_pcm16[offset : offset + 3200]
                await runtime.feed_audio(pcm16le_to_f32le(chunk))
                sent_frames += len(chunk) // 2
                samples.sample(runtime.metrics_snapshot())
                delay = collector.started_at + sent_frames / 16000 - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            drain_started_at = time.perf_counter()
            await runtime.end_audio()
            engine = runtime.engine
            worker = getattr(engine, "_worker_task", None)
            if engine is None or worker is None:
                raise RuntimeError("ASR worker absent after audio end")
            await asyncio.shield(worker)
            if engine.failure:
                raise RuntimeError(f"ASR inference failed: {engine.failure}")
            await runtime.pipeline._queue.join()
            await runtime._persistence_queue.join()
            delivery = runtime.hub._clients.get(collector)
            if delivery is None:
                raise RuntimeError("audience observer disconnected before drain")
            await delivery.queue.join()
            drained = True
            drain_seconds = time.perf_counter() - drain_started_at
        telemetry = runtime.metrics_snapshot().model_dump(mode="json")
        if runtime.pipeline.status.error:
            failure = f"translation reported error: {runtime.pipeline.status.error}"
    except asyncio.CancelledError:
        failure = "benchmark cancelled"
        raise
    except Exception as exc:
        failure = f"{type(exc).__name__}: {str(exc)[:1000]}"
    finally:
        if drain_started_at is not None and drain_seconds is None:
            drain_seconds = time.perf_counter() - drain_started_at
        try:
            telemetry = runtime.metrics_snapshot().model_dump(mode="json")
            samples.sample(runtime.metrics_snapshot())
            if runtime.engine is not None:
                asr_diagnostics = {
                    "timestamp_recovery_count": getattr(
                        runtime.engine, "timestamp_recovery_count", 0
                    ),
                    "timestamp_recovery_errors": getattr(
                        runtime.engine, "timestamp_recovery_errors", []
                    )[:32],
                }
        except Exception as exc:
            failure = failure or f"telemetry: {type(exc).__name__}: {str(exc)[:1000]}"
        try:
            await asyncio.wait_for(runtime.shutdown(), min(timeout_seconds, 30.0))
        except Exception as exc:
            failure = failure or f"runtime shutdown: {type(exc).__name__}: {str(exc)[:1000]}"
        if memory_task:
            memory_task.cancel()
            try:
                await memory_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                failure = failure or f"sampler: {type(exc).__name__}: {str(exc)[:1000]}"
        try:
            memory.sample(force=True)
            shutdown_telemetry = runtime.metrics_snapshot().model_dump(mode="json")
        except Exception as exc:
            failure = failure or f"final sampling: {type(exc).__name__}: {str(exc)[:1000]}"
        try:
            rows = collector.events if collector else []
            elapsed = time.perf_counter() - collector.started_at if collector else 0.0
            metrics = summarize_events(
                rows,
                audio_seconds,
                elapsed,
                source_language=source_language,
                target_language=target_language,
            )
            if metrics["invalid_timestamp_count"]:
                failure = failure or "out-of-bounds source timestamps; latency cannot be trusted"
            if not metrics["stable_count"] or not metrics["translated_committed_count"]:
                failure = failure or "no actual ASR -> translated committed captions"
            if collector and collector.overflow:
                failure = failure or "audience observation budget exceeded"
            report = {
                "schema_version": 2,
                "status": "runtime-error" if failure else "measured",
                "error": failure,
                "measurement_mode": measurement_mode,
                "audio_dataset": "google/fleurs",
                "revision": FLEURS_REVISION,
                "license": FLEURS_LICENSE,
                "source": FLEURS_DATASET,
                "source_language": source_language,
                "target_language": target_language,
                "audio_sha256_pcm16_combined": hashlib.sha256(audio_pcm16).hexdigest(),
                "clips": clips,
                "audio_seconds": round(audio_seconds, 3),
                "model": model,
                "decode_window_seconds": chunk_seconds,
                "device": "cpu",
                "compute_type": "int8",
                "translation_provider": translation_provider,
                "translation_model": runtime.pipeline.status.model,
                "model_provenance": provenance,
                "startup_seconds": cold_start_seconds,
                "reference_transcript": reference,
                "asr_quality": quality_metrics(reference, metrics["asr_hypothesis"]),
                "translation_quality": {
                    "status": "unavailable",
                    "chrf": None,
                    "reason": (
                        "No independently aligned human target reference; "
                        "FLEURS cross-language IDs are not assumed aligned"
                    ),
                },
                "metrics": metrics,
                "telemetry": telemetry,
                "shutdown_telemetry": shutdown_telemetry,
                "asr_diagnostics": asr_diagnostics,
                "queue_peaks": {
                    "postprocess": samples.postprocess_queue_peak,
                    "persistence": samples.persistence_queue_peak,
                },
                "runtime_stage_latency_ms": {
                    "asr": latency_summary(samples.asr_latency_ms),
                    "correction": latency_summary(samples.correction_latency_ms),
                    "translation": latency_summary(samples.translation_latency_ms),
                    "commit": latency_summary(samples.commit_latency_ms),
                },
                "runtime_budget": {
                    "asr_queue_chunks": settings.audio_queue_chunks,
                    "postprocess_workers": settings.postprocess_max_parallel_segments,
                    "parallel_target_limit": settings.translation_max_parallel_targets,
                    "target_timeout_seconds": settings.translation_target_timeout_seconds,
                    "m2m100_cpu_threads": settings.local_m2m100_cpu_threads,
                    "feed_and_drain_deadline_seconds": audio_seconds + timeout_seconds,
                },
                "queues_drained": drained,
                "drain_seconds": drain_seconds,
                "python_process_rss": memory.report(),
                "stages": rows,
                "audience_observation": {
                    "surface": "in_process_hub_send_json",
                    "network_or_browser_paint": False,
                    "session_id": collector.session_id if collector else None,
                    "source_language": source_language,
                    "target_languages": [target_language],
                    "snapshots": collector.snapshots if collector else 0,
                    "closed_on_cleanup": collector.closed if collector else None,
                    "event_limit": MAX_EVENTS,
                    "events_dropped": collector.overflow if collector else 0,
                    "scope_mismatches": collector.scope_errors if collector else 0,
                },
                "notes": [
                    "Licensed read speech; not a church/microphone/4GB-8GB field qualification",
                    "Manual studies use real inference; unit tests are labeled mock-wiring",
                    "Hub observations exclude ASGI authorization, network and browser paint",
                ],
            }
            write_report(output, report)
        except Exception as exc:
            write_report(
                output,
                {
                    "schema_version": 2,
                    "status": "report-error",
                    "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
                    "source_language": source_language,
                    "target_language": target_language,
                    "audio_sha256_pcm16_combined": hashlib.sha256(audio_pcm16).hexdigest(),
                    "dataset_revision": FLEURS_REVISION,
                    "field_ready": False,
                },
            )
            raise
    if failure:
        raise RuntimeError(f"live interpretation pipeline failed: {failure}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--model", choices=["tiny", "base", "small"], default="base")
    parser.add_argument("--chunk-seconds", type=int, choices=[4, 8, 15], default=8)
    parser.add_argument("--max-drain-seconds", type=float, default=240)
    parser.add_argument("--source-language", choices=["ko", "en"], default="ko")
    parser.add_argument(
        "--translation-provider", choices=["ollama", "m2m100-ct2"], default="ollama"
    )
    parser.add_argument("--asr-model-dir", type=Path)
    parser.add_argument("--ct2-dir", type=Path)
    parser.add_argument("--tokenizer-dir", type=Path)
    parser.add_argument("--model-manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-church-cpu"))
    args = parser.parse_args()
    if not 30 <= args.max_drain_seconds <= 600:
        parser.error("max drain seconds must be 30 to 600")
    try:
        # A failed invocation must never upload a previous run's successful report.
        for name in ("live-interpretation-report.json", "failure-report.json"):
            (args.output / name).unlink(missing_ok=True)
        pcm, clips, reference = load_pinned_speech(args.samples, args.output, args.source_language)
        report = asyncio.run(
            run_live(
                pcm,
                reference,
                clips,
                args.output,
                model=args.model,
                chunk_seconds=float(args.chunk_seconds),
                timeout_seconds=args.max_drain_seconds,
                source_language=args.source_language,
                translation_provider=args.translation_provider,
                asr_model_dir=args.asr_model_dir,
                ct2_dir=args.ct2_dir,
                tokenizer_dir=args.tokenizer_dir,
                model_manifest=args.model_manifest,
            )
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "asr_quality": report["asr_quality"],
                    "metrics": report["metrics"],
                    "translation_quality": report["translation_quality"],
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        failure = {
            "schema_version": 2,
            "status": "runtime-error",
            "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
            "audio_dataset": "google/fleurs",
            "revision": FLEURS_REVISION,
            "source_language": args.source_language,
            "translation_provider": args.translation_provider,
            "field_ready": False,
        }
        if not (args.output / "live-interpretation-report.json").exists():
            write_report(args.output, failure)
        raise


if __name__ == "__main__":
    main()
