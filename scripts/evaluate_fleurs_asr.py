"""Reproducible CPU ASR evaluation against licensed, human-transcribed real speech.

Audio and transcripts are obtained from the pinned Google FLEURS CC-BY-4.0
test split. Nothing is committed to the repository. Requires the optional
benchmark/whisper extras and separately installed datasets/soundfile.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

FLEURS_REVISION = "fdd74f7cdf01fc7641ab3072b6a38d364b3667f6"
FLEURS_LICENSE = "https://creativecommons.org/licenses/by/4.0/"
FLEURS_DATASET = "https://huggingface.co/datasets/google/fleurs"
MAX_SAMPLE_SECONDS = 20
CONFIGS = {"ko": "ko_kr", "en": "en_us"}


def fleurs_test_parquet_uri(language: str) -> str:
    """Select only verified FLEURS language/test parquet from the pinned revision."""
    if language not in CONFIGS:
        raise ValueError("language must be ko or en")
    return (
        f"hf://datasets/google/fleurs@{FLEURS_REVISION}/"
        f"{CONFIGS[language]}/test-00000-of-00001.parquet"
    )


def audio_input(audio: dict) -> io.BytesIO | Path:
    """Accept decoded-off HF audio bytes or an actual local fixture path.

    Never fetch arbitrary audio URLs or guess a remote path: the dataset
    streaming library is responsible for retrieving authorized audio.
    """
    raw = audio.get("bytes")
    if isinstance(raw, (bytes, bytearray, memoryview)) and raw:
        return io.BytesIO(raw)
    path = audio.get("path")
    if isinstance(path, str) and path and not path.startswith(("http:", "https:")):
        candidate = Path(path)
        if candidate.is_file():
            return candidate
    raise ValueError("FLEURS fixture must provide audio bytes or a local readable file")


def write_pcm_wav(audio: dict, destination: Path) -> tuple[float, str]:
    import numpy as np
    import soundfile as sf

    samples, rate = sf.read(audio_input(audio), dtype="float32", always_2d=False)
    if rate != 16_000:
        raise ValueError(f"expected pinned FLEURS 16000 Hz, got {rate}")
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if samples.ndim != 1 or not np.isfinite(samples).all():
        raise ValueError("invalid nonfinite/complex fixture samples")
    duration = len(samples) / rate
    if not 0.25 <= duration <= MAX_SAMPLE_SECONDS:
        raise ValueError("audio clip outside fixture duration bounds")
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(destination), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(data)
    return duration, hashlib.sha256(data).hexdigest()


def evaluate(
    language: str,
    sample_count: int,
    output: Path,
    *,
    model: str = "tiny",
) -> dict:
    if language not in CONFIGS:
        raise ValueError("language must be ko or en")
    if not 1 <= sample_count <= 8:
        raise ValueError("samples must be between 1 and 8")
    if model not in {"tiny", "base", "small"}:
        raise ValueError("CPU model must be tiny, base or small")

    from datasets import Audio, load_dataset

    output.mkdir(parents=True, exist_ok=True)
    fixture_dir = output / "temporary-fixtures"
    fixture_dir.mkdir(exist_ok=True)

    # Datasets >=4 rejects legacy dataset loader scripts (fleurs.py).
    # Access only the verified parquet fixture at the pinned HF revision.
    parquet_path = fleurs_test_parquet_uri(language)
    dataset = load_dataset(
        "parquet",
        data_files={"test": parquet_path},
        split="test",
        streaming=True,
    ).cast_column("audio", Audio(decode=False))

    rows = []
    for attempt, sample in enumerate(dataset):
        if len(rows) >= sample_count:
            break
        if attempt >= 40:
            raise RuntimeError("insufficient eligible audio among first 40 test samples")
        identifier = sample.get("id")
        text = str(sample.get("raw_transcription") or sample.get("transcription") or "").strip()
        if not text:
            continue
        wav_path = fixture_dir / f"sample-{len(rows):03d}.wav"
        try:
            duration, sha = write_pcm_wav(sample["audio"], wav_path)
        except (ValueError, TypeError):
            wav_path.unlink(missing_ok=True)
            continue
        reference_path = wav_path.with_suffix(".txt")
        reference_path.write_text(text, encoding="utf-8")
        report_path = output / f"report-{len(rows):03d}.json"
        command = [
            sys.executable, "-m", "langtextflow.benchmark", str(wav_path),
            "--engine", "faster-whisper", "--source-language", language,
            "--pace", "max", "--chunk-ms", "100",
            "--settle-seconds", "0.5", "--drain-timeout-seconds", "60",
            "--reference", str(reference_path), "--output", str(report_path),
            "--title", "FLEURS test-set real speech evaluation",
        ]
        completed = subprocess.run(
            command,
            text=True, capture_output=True,
            check=False, timeout=240,
            env={
                **os.environ,
                "LANGTEXTFLOW_FASTER_WHISPER_MODEL": model,
                "LANGTEXTFLOW_FASTER_WHISPER_DEVICE": "cpu",
                "LANGTEXTFLOW_FASTER_WHISPER_COMPUTE_TYPE": "int8",
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"ASR benchmark failed for FLEURS item {identifier}: "
                f"{completed.stderr[-1000:]} {completed.stdout[-1000:]}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report["status"] != "ok" or not report["quality"]["hypothesis_characters"]:
            raise RuntimeError(f"ASR provider emitted no valid transcript for item {identifier}")
        rows.append({
            "id": identifier,
            "audio_duration_seconds": duration,
            "sha256_pcm16": sha,
            "reference_characters": len(text),
            "cer": report["quality"]["cer"],
            "wer": report["quality"]["wer"],
            "throughput_rtf": report["timing"]["throughput_rtf"],
            "queue_high_watermark": report["provider"]["queue_high_watermark"],
            "report": str(report_path),
        })
        wav_path.unlink(missing_ok=True)
        reference_path.unlink(missing_ok=True)

    if len(rows) != sample_count:
        raise RuntimeError(
            f"Only {len(rows)} eligible FLEURS samples found; requested {sample_count}"
        )

    summary = {
        "dataset": "google/fleurs",
        "revision": FLEURS_REVISION,
        "split": "test",
        "license": "CC-BY-4.0",
        "license_url": FLEURS_LICENSE,
        "source_url": FLEURS_DATASET,
        "generated_at": datetime.now(UTC).isoformat(),
        "language": language,
        "model": model,
        "sample_count": len(rows),
        "mean_cer": sum(row["cer"] for row in rows) / len(rows),
        "mean_wer": sum(row["wer"] for row in rows) / len(rows),
        "mean_throughput_rtf": sum(row["throughput_rtf"] for row in rows) / len(rows),
        "samples": rows,
        "note": (
            "CPU file-replay read-speech pilot; not live interpretation throughput, "
            "translation quality, or a GPU/field acceptance result."
        ),
    }
    path = output / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real-audio FLEURS ASR evaluation")
    parser.add_argument("--language", required=True, choices=CONFIGS)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--model", choices=["tiny", "base", "small"], default="tiny")
    parser.add_argument("--output", type=Path, default=Path("artifacts/fleurs-asr"))
    args = parser.parse_args()
    report = evaluate(args.language, args.samples, args.output, model=args.model)
    print(json.dumps({
        "language": report["language"],
        "mean_cer": report["mean_cer"],
        "mean_wer": report["mean_wer"],
        "mean_throughput_rtf": report["mean_throughput_rtf"],
        "reports": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
