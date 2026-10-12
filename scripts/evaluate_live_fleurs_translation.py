"""Real-time-paced, licensed FLEURS audio through LangTextFlow's *actual* ASR
-> caption correction -> Ollama translation -> committed caption pipeline.

This does not equate FLEURS read speech with a real church service. FLEURS has
human transcription (source ASR gold) but NOT human Korean->English translation.
Translation quality therefore requires the separate #108 human review gate.
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

from langtextflow.benchmark import pcm16le_to_f32le, percentile, quality_metrics
from langtextflow.config import Settings
from langtextflow.models import SessionContext, StartSessionRequest
from langtextflow.runtime import CaptionRuntime

from scripts.evaluate_fleurs_asr import (
    FLEURS_DATASET, FLEURS_LICENSE, FLEURS_REVISION,
    fleurs_test_parquet_uri, write_pcm_wav,
)


def summarize_events(
    events: list[dict[str, Any]], audio_seconds: float, wall_seconds: float,
) -> dict[str, Any]:
    """Pure aggregation: untranslated source captions never count as translated."""
    if audio_seconds <= 0:
        raise ValueError("audio_seconds must be positive")
    by_stage: dict[str, list[dict[str, Any]]] = {
        stage: [row for row in events if row["stage"] == stage]
        for stage in ("stable", "translated", "committed")
    }
    committed = sorted(
        by_stage["committed"], key=lambda row: (row.get("start_ms") or 0, row["segment_id"])
    )
    translations = [
        row for row in committed if str(row.get("translations", {}).get("en", "")).strip()
    ]
    invalid = {
        str(row["segment_id"]) for row in events
        if isinstance(row.get("end_ms"), (int, float))
        and (
            row["end_ms"] < 0
            or row["end_ms"] > audio_seconds * 1000 + 250
            or row.get("start_ms", 0) is not None
            and (
                row.get("start_ms", 0) < 0
                or row.get("start_ms", 0) > row["end_ms"]
            )
            or row["end_ms"] > row["received_ms"] + 250
        )
    }
    latencies = {
        stage: [
            max(0.0, row["received_ms"] - row["end_ms"])
            for row in rows
            if isinstance(row.get("end_ms"), (int, float))
            and str(row["segment_id"]) not in invalid
        ]
        for stage, rows in by_stage.items()
    }
    return {
        "stable_count": len(by_stage["stable"]),
        "committed_count": len(committed),
        "translated_committed_count": len(translations),
        "translation_coverage": round(len(translations) / len(committed), 4)
        if committed else 0.0,
        "asr_hypothesis": " ".join(row["text"] for row in committed),
        "translated_hypothesis": " ".join(row["translations"]["en"] for row in translations),
        "p95_lag_ms": {
            stage: percentile(latencies[stage], 0.95) for stage in latencies
        },
        "first_translation_ms": min(
            (row["received_ms"] for row in by_stage["translated"]), default=None
        ),
        "processing_rtf": round(wall_seconds / audio_seconds, 4),
        "translation_backlog_warning": (
            not committed or len(translations) != len(committed)
        ),
        "invalid_timestamp_segment_ids": sorted(invalid),
        "invalid_timestamp_count": len(invalid),
        # All acceptance depends on ASR quality, semantic review and live hardware.
        "field_ready": False,
    }


def load_pinned_speech(
    count: int, output: Path,
) -> tuple[bytes, list[dict[str, Any]], str]:
    from datasets import Audio, load_dataset

    if not 1 <= count <= 3:
        raise ValueError("sample_count must be between 1 and 3")
    dataset = load_dataset(
        "parquet",
        data_files={"test": fleurs_test_parquet_uri("ko")},
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
            raise RuntimeError("fewer than 3 eligible pinned FLEURS clips")
        sentence = str(
            sample.get("raw_transcription") or sample.get("transcription") or ""
        ).strip()
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
            pcm_bytes.extend(b"\x00" * 16_000)  # 0.5 seconds silence PCM16
        pcm_bytes.extend(frames)
        sentences.append(sentence)
        metadata.append({
            "id": sample.get("id"), "duration_seconds": seconds,
            "sha256_pcm16": digest, "reference": sentence,
        })
    if len(metadata) != count:
        raise RuntimeError(f"insufficient FLEURS speech samples: {len(metadata)}/{count}")
    return bytes(pcm_bytes), metadata, " ".join(sentences)


async def run_live(
    audio_pcm16: bytes, reference: str, clips: list[dict[str, Any]],
    output: Path, *, model: str, chunk_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not audio_pcm16 or len(audio_pcm16) % 2:
        raise ValueError("expected non-empty 16-bit mono PCM audio")
    if model not in {"tiny", "base", "small"} or chunk_seconds not in {4.0, 8.0, 15.0}:
        raise ValueError("unsupported controlled ASR comparison configuration")
    audio_seconds = len(audio_pcm16) / (16_000 * 2)
    settings = Settings(
        _env_file=None,
        database_path=str(output / "live-benchmark.sqlite"),
        faster_whisper_model=model,
        faster_whisper_device="cpu",
        faster_whisper_compute_type="int8",
        faster_whisper_chunk_seconds=chunk_seconds,
        audio_queue_chunks=16,
        max_segments=256,
        postprocess_drain_timeout_seconds=30.0,
        ollama_translation_timeout_seconds=180.0,
    )
    runtime = CaptionRuntime(settings)
    records: list[dict[str, Any]] = []
    started_at: float | None = None
    original_publish = runtime._publish

    async def capture(event: Any) -> None:
        if started_at is not None:
            records.append({
                "segment_id": event.segment_id,
                "stage": event.stage.value,
                "version": event.version,
                "text": event.text,
                "start_ms": event.start_ms,
                "end_ms": event.end_ms,
                "received_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "translations": dict(event.translations),
            })
        await original_publish(event)

    # Capture events from ASR's real CaptionPipeline and its actual broadcaster.
    runtime.pipeline.publish = capture
    request = StartSessionRequest(
        source_language="ko", target_languages=["en"],
        engine="faster-whisper", translation_provider="ollama",
        translation_model="translategemma:4b", correction_provider="none",
        context=SessionContext(title="Licensed read-speech pilot", audience_access=False),
    )
    startup = time.perf_counter()
    started = False
    failure: str | None = None
    telemetry: dict[str, Any] = {}
    try:
        await runtime.start(request)
        started = True
        cold_start_seconds = round(time.perf_counter() - startup, 3)
        if not runtime.pipeline.status.available:
            raise RuntimeError(
                f"translation unavailable: {runtime.pipeline.status.error}"
            )
        started_at = time.perf_counter()
        sent_frames = 0
        # Real-time pacing, not fastest-possible file replay.
        for offset in range(0, len(audio_pcm16), 3_200):  # 100 ms PCM16
            pcm16_chunk = audio_pcm16[offset:offset + 3_200]
            await runtime.feed_audio(pcm16le_to_f32le(pcm16_chunk))
            sent_frames += len(pcm16_chunk) // 2
            target = started_at + sent_frames / 16_000
            delay = target - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        await runtime.end_audio()
        active_engine = runtime.engine
        if active_engine is None:
            raise RuntimeError("no ASR engine after audio end")
        asr_worker = getattr(active_engine, "_worker_task", None)
        if asr_worker is None:
            raise RuntimeError("ASR worker task absent")
        await asyncio.wait_for(asyncio.shield(asr_worker), timeout_seconds)
        if active_engine.failure:
            raise RuntimeError(f"ASR inference failed: {active_engine.failure}")
        await asyncio.wait_for(runtime.pipeline._queue.join(), timeout_seconds)
        if runtime.pipeline.status.error:
            failure = f"translation reported error: {runtime.pipeline.status.error}"
        telemetry = runtime.metrics_snapshot().model_dump(mode="json")
    except Exception as exc:
        failure = str(exc)
        if started:
            telemetry = runtime.metrics_snapshot().model_dump(mode="json")
    finally:
        if started:
            try:
                await runtime.stop()
            except Exception as exc:
                failure = failure or f"runtime shutdown: {exc}"
        await runtime.shutdown()
    if started_at is None:
        raise RuntimeError(f"session failed before audio input: {failure}")
    wall_seconds = time.perf_counter() - started_at
    metrics = summarize_events(records, audio_seconds, wall_seconds)
    asr_quality = quality_metrics(reference, metrics["asr_hypothesis"])
    if metrics["invalid_timestamp_count"]:
        failure = failure or (
            f"out-of-bounds source timestamp in {metrics['invalid_timestamp_count']} "
            "segments; lag cannot be trusted"
        )
    report = {
        "schema_version": 1, "status": "measured" if not failure else "runtime-error",
        "error": failure,
        "audio_dataset": "google/fleurs", "revision": FLEURS_REVISION,
        "license": FLEURS_LICENSE, "source": FLEURS_DATASET,
        "source_language": "ko", "target_language": "en",
        "audio_sha256_pcm16_combined": hashlib.sha256(audio_pcm16).hexdigest(),
        "clips": clips, "audio_seconds": round(audio_seconds, 3),
        "model": model, "decode_window_seconds": chunk_seconds,
        "translation_model": "translategemma:4b",
        "startup_seconds": cold_start_seconds,
        "reference_transcript": reference,
        "asr_quality": asr_quality,
        "metrics": metrics, "telemetry": telemetry,
        "stages": records,
        "notes": [
            "Real FLEURS read speech, NOT a church service or a microphone field test",
            "English output has NO human translated gold reference: only coverage and lag are measured here",
            "Quality of translation model on supplied correct text measured separately in PR #108",
            "This run is not proof of acceptable p95 live interpretation latency",
            "End-to-end inference competes for the same CPU and RAM; no GPU assumed",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "live-interpretation-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failure:
        raise RuntimeError(f"live interpretation pipeline failed: {failure}")
    if not metrics["stable_count"] or not metrics["translated_committed_count"]:
        raise RuntimeError("no actual ASR -> English translated committed captions")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--model", choices=["tiny", "base", "small"], default="base")
    parser.add_argument("--chunk-seconds", type=int, choices=[4, 8, 15], default=8)
    parser.add_argument("--max-drain-seconds", type=float, default=240)
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-church-cpu"))
    args = parser.parse_args()
    if not 30 <= args.max_drain_seconds <= 600:
        parser.error("max drain seconds must be 30 to 600")
    output = args.output
    pcm, clips, reference = load_pinned_speech(args.samples, output)
    report = asyncio.run(run_live(
        pcm, reference, clips, output, model=args.model,
        chunk_seconds=float(args.chunk_seconds),
        timeout_seconds=args.max_drain_seconds,
    ))
    print(json.dumps({
        "status": report["status"],
        "asr_cer": report["asr_quality"]["cer"],
        "asr_wer": report["asr_quality"]["wer"],
        "translation_coverage": report["metrics"]["translation_coverage"],
        "p95_live_lag_ms": report["metrics"]["p95_lag_ms"],
        "processing_rtf": report["metrics"]["processing_rtf"],
        "field_ready": False,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
