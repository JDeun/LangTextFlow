"""CPU-only Korean ASR challenger benchmark against the exact pinned FLEURS corpus.

Model weights are provisioned by CI outside the repository; do not commit audio.
Separate ASR quality from translated caption quality and genuine online streaming.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from langtextflow.benchmark import quality_metrics
from scripts.evaluate_fleurs_asr import (
    FLEURS_DATASET,
    FLEURS_LICENSE,
    FLEURS_REVISION,
    fleurs_test_parquet_uri,
    write_pcm_wav,
)

CANDIDATES = (
    "sensevoice",
    "qwen3-0.6b",
    "zipformer-ko",
    "whispercpp-base",
    "qwen3-1.7b",
)


def model_requirements(name: str) -> tuple[str, ...]:
    configs = {
        "sensevoice": ("model.int8.onnx", "tokens.txt"),
        "qwen3-0.6b": (
            "conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx",
            "tokenizer",
        ),
        "qwen3-1.7b": (
            "conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx",
            "tokenizer",
        ),
        "zipformer-ko": (
            "tokens.txt", "encoder-epoch-99-avg-1.int8.onnx",
            "decoder-epoch-99-avg-1.onnx",
            "joiner-epoch-99-avg-1.int8.onnx",
        ),
        "whispercpp-base": ("ggml-base.bin",),
    }
    return configs[name]


def validate_model_files(name: str, model_dir: Path) -> None:
    missing = [
        item for item in model_requirements(name)
        if not (model_dir / item).exists()
    ]
    if missing:
        raise FileNotFoundError(f"{name}: missing files {missing}")


class ResourceSamples:
    """Collect the real benchmark Python RSS/CPU; subprocess metrics too."""

    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ResourceSamples":
        import psutil

        process = psutil.Process()

        def poll() -> None:
            while not self._stop.is_set():
                processes = [process]
                try:
                    processes += process.children(recursive=True)
                except psutil.Error:
                    pass
                rss = 0
                for item in processes:
                    try:
                        rss += item.memory_info().rss
                    except psutil.Error:
                        continue
                self.samples.append({
                    "time": round(time.monotonic(), 3),
                    "rss_mb": round(rss / (1024 ** 2), 3),
                })
                self._stop.wait(0.1)

        self._thread = threading.Thread(target=poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def summary(self) -> dict[str, Any]:
        return {
            "rss_peak_mb": max((r["rss_mb"] for r in self.samples), default=None),
            "sample_count": len(self.samples),
        }


def create_recognizer(name: str, root: Path, threads: int) -> Any:
    import sherpa_onnx

    validate_model_files(name, root)
    if name == "sensevoice":
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(root / "model.int8.onnx"),
            tokens=str(root / "tokens.txt"),
            language="ko",
            num_threads=threads,
            use_itn=False,
            provider="cpu",
        )
    if name.startswith("qwen3-"):
        return sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            conv_frontend=str(root / "conv_frontend.onnx"),
            encoder=str(root / "encoder.int8.onnx"),
            decoder=str(root / "decoder.int8.onnx"),
            tokenizer=str(root / "tokenizer"),
            max_new_tokens=512,
            num_threads=threads,
            provider="cpu",
        )
    if name == "zipformer-ko":
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(root / "tokens.txt"),
            encoder=str(root / "encoder-epoch-99-avg-1.int8.onnx"),
            decoder=str(root / "decoder-epoch-99-avg-1.onnx"),
            joiner=str(root / "joiner-epoch-99-avg-1.int8.onnx"),
            num_threads=threads,
            decoding_method="greedy_search",
            enable_endpoint_detection=False,
            provider="cpu",
        )
    raise ValueError(f"unknown sherpa ASR: {name}")


def decode_sample(
    name: str,
    model: Any,
    wav_path: Path,
    *,
    threads: int,
    pace: str,
    whisper_cli: Path | None = None,
) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    audio, rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if rate != 16_000 or audio.ndim != 1:
        raise ValueError("benchmark wav must be 16kHz mono")
    seconds = len(audio) / rate
    started = time.perf_counter()
    first_partial_ms: float | None = None
    inference_seconds = 0.0
    if name == "zipformer-ko":
        stream = model.create_stream()
        for offset in range(0, len(audio), 1600):  # 100ms incremental frame
            frame = audio[offset:offset + 1600]
            compute_started = time.perf_counter()
            stream.accept_waveform(rate, frame)
            while model.is_ready(stream):
                model.decode_stream(stream)
            partial = model.get_result(stream)
            inference_seconds += time.perf_counter() - compute_started
            if partial.strip() and first_partial_ms is None:
                first_partial_ms = round((time.perf_counter() - started) * 1000, 1)
            if pace == "realtime":
                target = started + (offset + len(frame)) / rate
                if target > time.perf_counter():
                    time.sleep(target - time.perf_counter())
        tail_started = time.perf_counter()
        stream.accept_waveform(rate, np.zeros(8000, dtype=np.float32))
        stream.input_finished()
        while model.is_ready(stream):
            model.decode_stream(stream)
        hypothesis = model.get_result(stream).strip()
        inference_seconds += time.perf_counter() - tail_started
    else:
        # Offline recognizers see each complete utterance only at its end.
        # Realtime pacing is charged to overall caption lag, NOT inference RTF.
        if pace == "realtime":
            time.sleep(seconds)
        compute_started = time.perf_counter()
        if name == "whispercpp-base":
            if whisper_cli is None or not whisper_cli.is_file():
                raise FileNotFoundError("missing compiled whisper-cli")
            with tempfile.TemporaryDirectory(prefix="ltf-whisper-result-") as tmp:
                destination = Path(tmp) / "transcript"
                run = subprocess.run(
                    [
                        str(whisper_cli), "-m", str(model), "-f", str(wav_path),
                        "-l", "ko", "-t", str(threads), "-otxt",
                        "-of", str(destination),
                    ],
                    capture_output=True, text=True, timeout=180, check=False,
                )
                if run.returncode:
                    raise RuntimeError(f"whisper-cli failed: {run.stderr[-800:]}")
                hypothesis = destination.with_suffix(".txt").read_text(
                    encoding="utf-8"
                ).strip()
        else:
            stream = model.create_stream()
            stream.accept_waveform(rate, audio)
            model.decode_stream(stream)
            hypothesis = stream.result.text.strip()
        inference_seconds = time.perf_counter() - compute_started
    elapsed = time.perf_counter() - started
    return {
        "hypothesis": hypothesis,
        "audio_duration_seconds": round(seconds, 3),
        "end_to_end_wall_seconds": round(elapsed, 3),
        "end_to_end_rtf": round(elapsed / seconds, 4),
        "estimated_caption_lag_ms": round(
            max(0.0, (elapsed - seconds) * 1000), 1
        ) if pace == "realtime" else None,
        "first_partial_ms": first_partial_ms,
        "inference_seconds": round(inference_seconds, 4),
        "inference_rtf": round(inference_seconds / seconds, 4),
        "streaming_kind": (
            "native_incremental" if name == "zipformer-ko"
            else "offline_utterance"
        ),
    }


def load_three_pinned_fleurs() -> list[dict[str, Any]]:
    from datasets import Audio, load_dataset

    ds = load_dataset(
        "parquet",
        data_files={"test": fleurs_test_parquet_uri("ko")},
        split="test", streaming=True,
    ).cast_column("audio", Audio(decode=False))
    selected = []
    for attempt, row in enumerate(ds):
        if len(selected) >= 3:
            break
        if attempt >= 40:
            break
        text = str(row.get("raw_transcription") or row.get("transcription") or "").strip()
        if text and isinstance(row.get("audio"), dict):
            with tempfile.TemporaryDirectory(prefix="ltf-fleurs-filter-") as tmp:
                try:
                    write_pcm_wav(row["audio"], Path(tmp) / "check.wav")
                except (ValueError, TypeError):
                    continue
            selected.append({"id": row.get("id"), "text": text, "audio": row["audio"]})
    if len(selected) != 3:
        raise RuntimeError("expected 3 eligible Korean FLEURS samples")
    return selected


def evaluate(
    name: str, root: Path, destination: Path,
    *, threads: int, pace: str, whisper_cli: Path | None = None,
) -> dict[str, Any]:
    if name not in CANDIDATES:
        raise ValueError(f"invalid ASR candidate: {name}")
    if not 1 <= threads <= 4:
        raise ValueError("CPU thread limit must be 1-4")
    if pace not in {"max", "realtime"}:
        raise ValueError("pace must be max or realtime")
    validate_model_files(name, root)
    destination.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    with ResourceSamples() as resources:
        started = time.perf_counter()
        model = (
            str(root / "ggml-base.bin") if name == "whispercpp-base"
            else create_recognizer(name, root, threads)
        )
        cold_load_seconds = time.perf_counter() - started
        for i, item in enumerate(load_three_pinned_fleurs()):
            wav = destination / f"temporary-{i:03d}.wav"
            try:
                duration, audio_sha = write_pcm_wav(item["audio"], wav)
                result = decode_sample(
                    name, model, wav, threads=threads, pace=pace,
                    whisper_cli=whisper_cli,
                )
                result.update({
                    "id": item["id"],
                    "pcm_sha256": audio_sha,
                    "reference": item["text"],
                    "reference_characters": len(item["text"]),
                    "duration_seconds": duration,
                    "quality": quality_metrics(item["text"], result["hypothesis"]),
                })
                samples.append(result)
            finally:
                wav.unlink(missing_ok=True)
        del model
    if len(samples) != 3:
        raise RuntimeError(f"benchmark incomplete: {len(samples)}/3 clips")
    if not all(row["hypothesis"] for row in samples):
        raise RuntimeError("model emitted empty transcript for a FLEURS sample")
    return {
        "schema_version": 1, "candidate": name, "status": "measured",
        "real_model": True, "source_language": "ko",
        "dataset": "google/fleurs", "revision": FLEURS_REVISION,
        "dataset_uri": FLEURS_DATASET, "license": FLEURS_LICENSE,
        "python": platform.python_version(),
        "cpu_threads": threads, "pace": pace,
        "cold_load_seconds": round(cold_load_seconds, 3),
        "resources": resources.summary(),
        "mean_cer": round(
            sum(row["quality"]["cer"] for row in samples) / len(samples), 4
        ),
        "mean_wer": round(
            sum(row["quality"]["wer"] for row in samples) / len(samples), 4
        ),
        "mean_end_to_end_rtf": round(
            sum(row["end_to_end_rtf"] for row in samples) / len(samples), 4
        ),
        "mean_inference_rtf": round(
            sum(row["inference_rtf"] for row in samples) / len(samples), 4
        ),
        "samples": samples,
        "notice": (
            "Real recorded speech, independent source transcript; not "
            "a validated church sermon, translated captions, or field acceptance."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, choices=CANDIDATES)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--pace", choices=["max", "realtime"], default="realtime")
    parser.add_argument("--whisper-cli", type=Path)
    args = parser.parse_args()
    report = evaluate(
        args.candidate, args.model_dir, args.output.parent,
        threads=args.threads, pace=args.pace,
        whisper_cli=args.whisper_cli,
    )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "candidate": report["candidate"], "mean_cer": report["mean_cer"],
        "mean_wer": report["mean_wer"],
        "mean_end_to_end_rtf": report["mean_end_to_end_rtf"],
        "mean_inference_rtf": report["mean_inference_rtf"],
        "rss_peak_mb": report["resources"]["rss_peak_mb"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
