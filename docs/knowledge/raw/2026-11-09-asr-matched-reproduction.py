"""Relocatable POSIX reproduction companion; not the original executed driver.

Only local path bindings differ from the preserved private source. Native results
in the adjacent metadata belong to its separately recorded original SHA.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import secrets
import signal
import subprocess
import sys
import time
import wave
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ["LTF_ASR_EVIDENCE"]).resolve()
REPO = Path(os.environ["LTF_ASR_FROZEN_SOURCE"]).resolve()
SOURCE = "1fb5e24e136ce891b8f03c323f5a8c0f1b7ea6d5"
FLEURS = "fdd74f7cdf01fc7641ab3072b6a38d364b3667f6"
BASE = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
SMALL = "536b0662742c02347bc0e980a01041f333bce120"
MODELS = {
    "base": ROOT / "models/base-ebe41f70",
    "small": Path(os.environ["LTF_ASR_SMALL_MODEL"]).resolve(),
}
BASE_HASHES = {
    "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
    "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}
SMALL_MODEL = "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def clean_source():
    def git(*args):
        return subprocess.check_output(["git", "-C", str(REPO), *args], text=True).strip()

    if git("rev-parse", "HEAD") != SOURCE or git("status", "--porcelain"):
        raise RuntimeError("frozen_source_changed_or_dirty")
    return {
        name: sha(REPO / name)
        for name in (
            "apps/server/langtextflow/asr/faster_whisper.py",
            "apps/server/langtextflow/inference_worker.py",
            "apps/server/langtextflow/benchmark.py",
            "scripts/evaluate_fleurs_asr.py",
        )
    }


def product():
    clean_source()
    sys.path[:0] = [str(REPO / "apps/server"), str(REPO / "scripts")]
    from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
    from langtextflow.benchmark import pcm16le_to_f32le, quality_metrics
    from langtextflow.models import SessionContext, StartSessionRequest

    request = StartSessionRequest(
        engine="faster-whisper",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
        correction_provider="none",
        context=SessionContext(title="ASR prototype"),
    )
    return FasterWhisperStreamingAsrEngine, pcm16le_to_f32le, quality_metrics, request


def chunks(raw, window):
    size = window * 16000 * 2
    return [(start // 2, raw[start : start + size]) for start in range(0, len(raw), size)]


def verify_models():
    verified = {}
    for name, revision in (("base", BASE), ("small", SMALL)):
        metadata = json.loads((ROOT / f"official-{name}-files-{revision[:7]}.json").read_text())
        if (
            metadata["id"] != f"Systran/faster-whisper-{name}"
            or metadata["sha"] != revision
            or metadata["cardData"]["license"] != "mit"
        ):
            raise RuntimeError("official_model_metadata_mismatch")
        siblings = {row["rfilename"]: row for row in metadata["siblings"]}
        files = {}
        for filename in BASE_HASHES:
            path = MODELS[name] / filename
            actual = sha(path)
            upstream = siblings[filename]
            if upstream.get("lfs"):
                if actual != upstream["lfs"]["sha256"]:
                    raise RuntimeError("official_lfs_hash_mismatch")
            else:
                content = path.read_bytes()
                git_blob = hashlib.sha1(
                    b"blob " + str(len(content)).encode() + b"\0" + content
                ).hexdigest()
                if git_blob != upstream["blobId"]:
                    raise RuntimeError("official_git_blob_mismatch")
            if name == "base" and actual != BASE_HASHES[filename]:
                raise RuntimeError("expected_base_hash_mismatch")
            if name == "small" and filename == "model.bin" and actual != SMALL_MODEL:
                raise RuntimeError("expected_small_hash_mismatch")
            files[filename] = {"sha256": actual, "bytes": path.stat().st_size}
        if name == "base" and files["model.bin"]["bytes"] != 145217532:
            raise RuntimeError("expected_base_size_mismatch")
        verified[name] = {
            "repository": metadata["id"],
            "revision": revision,
            "license": "MIT",
            "files": files,
            "metadata_sha256": sha(ROOT / f"official-{name}-files-{revision[:7]}.json"),
        }
    return verified


def prepare():
    product()
    from datasets import Audio, load_dataset
    from evaluate_fleurs_asr import fleurs_test_parquet_uri, write_pcm_wav

    models = verify_models()
    if (ROOT / "fixtures.json").exists():
        raise RuntimeError("fixtures_already_prepared_do_not_overwrite")
    metadata = json.loads((ROOT / "official-fleurs-fdd74f7.json").read_text())
    licenses = metadata["cardData"]["license"]
    licenses = [licenses] if isinstance(licenses, str) else licenses
    if metadata["sha"] != FLEURS or licenses != ["cc-by-4.0"]:
        raise RuntimeError("dataset_revision_or_license_mismatch")
    selected_dir = ROOT / (
        "fixtures-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    )
    selected_dir.mkdir()
    selected, rejected = [], []
    dataset = load_dataset(
        "parquet",
        data_files={"test": fleurs_test_parquet_uri("ko")},
        split="test",
        streaming=True,
    ).cast_column("audio", Audio(decode=False))
    for attempt, sample in enumerate(dataset):
        if attempt >= 40 or len(selected) == 8:
            break
        text = str(sample.get("raw_transcription") or sample.get("transcription") or "").strip()
        if not text:
            rejected.append(
                {"attempt": attempt, "id": sample.get("id"), "reason": "empty_reference"}
            )
            continue
        path = selected_dir / f"candidate-{attempt:02d}.wav"
        try:
            duration, pcm_sha = write_pcm_wav(sample["audio"], path)
        except (TypeError, ValueError) as exc:
            rejected.append(
                {"attempt": attempt, "id": sample.get("id"), "reason": type(exc).__name__}
            )
            continue
        with wave.open(str(path), "rb") as stream:
            count = stream.getnframes()
            raw = stream.readframes(count)
        if hashlib.sha256(raw).hexdigest() != pcm_sha:
            raise RuntimeError("fixture_pcm_hash_mismatch")
        selected.append(
            {
                "selection_index": len(selected),
                "dataset_index": attempt,
                "id": sample.get("id"),
                "reference": text,
                "reference_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "path": str(path.relative_to(ROOT)),
                "sample_count": count,
                "duration_seconds": duration,
                "sha256_pcm16": pcm_sha,
                "sha256_wav": sha(path),
            }
        )
        print(
            json.dumps(
                {
                    "stage": "fixture",
                    "index": len(selected),
                    "id": sample.get("id"),
                    "duration_seconds": duration,
                }
            ),
            flush=True,
        )
    if len(selected) != 8:
        write_new(
            selected_dir / "preparation-failed.json", {"selected": selected, "rejected": rejected}
        )
        raise RuntimeError("insufficient_eligible_first_40")
    result = {
        "dataset": "google/fleurs",
        "revision": FLEURS,
        "language": "ko_kr",
        "split": "test",
        "license": "CC-BY-4.0",
        "source_url": "https://huggingface.co/datasets/google/fleurs/tree/" + FLEURS,
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "selection": "first_8_eligible_in_first_40; duration_0.25_to_20s; no_outcome_filter",
        "prepared_at": now(),
        "samples": selected,
        "rejected": rejected,
        "models": models,
        "source_commit": SOURCE,
        "source_files": clean_source(),
    }
    write_new(ROOT / "fixtures.json", result)
    return {
        "status": "prepared",
        "ids": [row["id"] for row in selected],
        "fixture_manifest_sha256": sha(ROOT / "fixtures.json"),
    }


def arm(plan_path, index):
    Engine, convert, quality, request = product()
    plan = json.loads(plan_path.read_text())
    if sha(__file__) != plan["driver_sha256"]:
        raise RuntimeError("driver_changed")
    spec = plan["arms"][index]
    output = plan_path.parent / f"arm-{index}-{spec['model']}"
    output.mkdir()
    manifest = json.loads((ROOT / "fixtures.json").read_text())
    if sha(ROOT / "fixtures.json") != plan["fixture_manifest_sha256"]:
        raise RuntimeError("fixture_manifest_changed")
    if verify_models() != plan["models"]:
        raise RuntimeError("model_files_changed")
    engine = Engine(
        lambda _: None,
        model=str(MODELS[spec["model"]]),
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        allow_download=False,
    )
    started = now()
    loadavg_start = list(os.getloadavg())
    before_load = time.perf_counter()
    engine._model = engine._load_model_sync()
    load_seconds = time.perf_counter() - before_load
    rows = []
    for window in spec["window_order"]:
        for clip in manifest["samples"]:
            wav_path = ROOT / clip["path"]
            if sha(wav_path) != clip["sha256_wav"]:
                raise RuntimeError("fixture_file_changed")
            with wave.open(str(wav_path), "rb") as audio:
                raw = audio.readframes(audio.getnframes())
            if hashlib.sha256(raw).hexdigest() != clip["sha256_pcm16"]:
                raise RuntimeError("fixture_pcm_changed")
            chunk_rows, hypothesis = [], []
            row = {
                "id": clip["id"],
                "window_seconds": window,
                "reference": clip["reference"],
                "sha256_pcm16": clip["sha256_pcm16"],
                "audio_duration_seconds": clip["duration_seconds"],
            }
            try:
                for offset, raw_chunk in chunks(raw, window):
                    engine._audio_cursor_samples = offset
                    recoveries = engine.timestamp_recovery_count
                    before = time.perf_counter()
                    try:
                        decoded = engine._transcribe_chunk_sync(convert(raw_chunk), request)
                    except Exception as exc:
                        chunk_rows.append(
                            {
                                "index": len(chunk_rows),
                                "start_sample": offset,
                                "end_sample": offset + len(raw_chunk) // 2,
                                "decode_seconds": time.perf_counter() - before,
                                "status": "failed",
                                "error_category": type(exc).__name__,
                            }
                        )
                        raise
                    elapsed = time.perf_counter() - before
                    text = " ".join(
                        segment.text.strip() for segment in decoded if segment.text.strip()
                    )
                    if text:
                        hypothesis.append(text)
                    chunk_rows.append(
                        {
                            "index": len(chunk_rows),
                            "start_sample": offset,
                            "end_sample": offset + len(raw_chunk) // 2,
                            "decode_seconds": elapsed,
                            "status": "ok",
                            "timestamp_recoveries": engine.timestamp_recovery_count - recoveries,
                            "segments": [vars(segment) for segment in decoded],
                        }
                    )
                text = " ".join(hypothesis)
                row.update(
                    status="ok",
                    hypothesis=text,
                    empty_hypothesis=not bool(text),
                    **quality(clip["reference"], text),
                )
            except Exception as exc:
                row.update(status="failed", error_category=type(exc).__name__, cer=None, wer=None)
            row["chunks"] = chunk_rows
            row["decode_seconds"] = sum(chunk["decode_seconds"] for chunk in chunk_rows)
            row["decode_rtf"] = row["decode_seconds"] / clip["duration_seconds"]
            write_new(output / f"clip-{clip['selection_index']:02d}-window-{window}.json", row)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "arm": index,
                        "model": spec["model"],
                        "window_seconds": window,
                        "id": clip["id"],
                        "status": row["status"],
                        "cer": row["cer"],
                        "wer": row["wer"],
                        "decode_seconds": row["decode_seconds"],
                    }
                ),
                flush=True,
            )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        "status": "completed",
        "arm": index,
        "model": spec["model"],
        "pid": os.getpid(),
        "started_at": started,
        "ended_at": now(),
        "host_loadavg_start": loadavg_start,
        "host_loadavg_end": list(os.getloadavg()),
        "window_order": spec["window_order"],
        "load_seconds": load_seconds,
        "process_lifetime_ru_maxrss_raw": peak,
        "process_lifetime_peak_rss_mib": peak / (1024 * 1024 if sys.platform == "darwin" else 1024),
        "rss_scope": (
            "child_process_lifetime_high_water_through_scoring_before_final_serialization; "
            "not process_tree"
        ),
        "source_commit": SOURCE,
        "source_files": clean_source(),
        "driver_sha256": sha(__file__),
        "fixture_manifest_sha256": plan["fixture_manifest_sha256"],
        "model_identity": plan["models"][spec["model"]],
        "rows": rows,
        "metrics_note": "per-chunk synchronous product decode wall time, not live caption latency",
    }
    write_new(output / "arm.json", report)


def run():
    clean_source()
    models = verify_models()
    manifest = json.loads((ROOT / "fixtures.json").read_text())
    if manifest["models"] != models:
        raise RuntimeError("prepared_model_identity_changed")
    run_dir = ROOT / (
        "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)
    )
    run_dir.mkdir()
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "faster-whisper",
            "ctranslate2",
            "numpy",
            "onnxruntime",
            "tokenizers",
            "datasets",
            "pyarrow",
            "huggingface-hub",
            "soundfile",
            "fsspec",
        )
    }
    versions["python"] = platform.python_version()
    plan = {
        "source_commit": SOURCE,
        "source_files": clean_source(),
        "driver_sha256": sha(__file__),
        "fixture_manifest_sha256": sha(ROOT / "fixtures.json"),
        "models": models,
        "versions": versions,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
        },
        "arms": [
            {"model": model, "window_order": [4, 8] if i < 2 else [8, 4]}
            for i, model in enumerate(("base", "small", "small", "base"))
        ],
        "controls": {
            "cpu_threads": 2,
            "num_workers": 1,
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 1,
            "vad_filter": True,
            "condition_on_previous_text": False,
            "initial_prompt": "ASR prototype",
            "glossary": [],
            "mt": "none",
            "native_random_seed": "product default; not overridden",
        },
        "limitations": [
            "KO read speech only, not sermon/domain semantics or six directions",
            "synchronous native decode, not runtime queues or browser/live caption latency",
            "same host; incidental development/system load is uncontrolled",
            "OS filesystem/model cache warmth is not equalized; ABBA is only drift mitigation",
            "process high-water RSS, not process-tree or physical 4/8GB qualification",
            "no model/default promotion or optimal combination claim",
        ],
    }
    write_new(run_dir / "plan.json", plan)
    deadline = time.monotonic() + 40 * 60
    results = []
    try:
        for index, spec in enumerate(plan["arms"]):
            budget = min(8 * 60, deadline - time.monotonic())
            if budget <= 0:
                raise TimeoutError("whole_experiment_budget")
            with (run_dir / f"arm-{index}-{spec['model']}.log").open("x", encoding="utf-8") as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        __file__,
                        "arm",
                        "--plan",
                        str(run_dir / "plan.json"),
                        "--index",
                        str(index),
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={
                        **os.environ,
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                        "HF_DATASETS_OFFLINE": "1",
                        "HF_HUB_DISABLE_TELEMETRY": "1",
                        "OMP_NUM_THREADS": "2",
                        "OPENBLAS_NUM_THREADS": "2",
                        "VECLIB_MAXIMUM_THREADS": "2",
                    },
                )
                print(
                    json.dumps(
                        {
                            "stage": "arm_started",
                            "arm": index,
                            "model": spec["model"],
                            "pid": child.pid,
                            "run": run_dir.name,
                        }
                    ),
                    flush=True,
                )
                try:
                    code = child.wait(timeout=budget)
                except BaseException:
                    if child.poll() is None:
                        with suppress(ProcessLookupError):
                            os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        if child.poll() is None:
                            with suppress(ProcessLookupError):
                                os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=3)
                    results.append(
                        {
                            "arm": index,
                            "model": spec["model"],
                            "pid": child.pid,
                            "returncode": child.returncode,
                            "report": None,
                            "error": "arm_interrupted_or_deadline",
                        }
                    )
                    raise
            artifact = run_dir / f"arm-{index}-{spec['model']}" / "arm.json"
            results.append(
                {
                    "arm": index,
                    "model": spec["model"],
                    "pid": child.pid,
                    "returncode": code,
                    "report": str(artifact.relative_to(run_dir)) if artifact.exists() else None,
                    "report_sha256": sha(artifact) if artifact.exists() else None,
                }
            )
            print(json.dumps({"stage": "arm_finished", **results[-1]}), flush=True)
    finally:
        write_new(
            run_dir / "summary.json",
            {
                "source_commit": SOURCE,
                "finished_at": now(),
                "status": "completed"
                if len(results) == 4 and all(r["returncode"] == 0 for r in results)
                else "incomplete",
                "results": results,
                "plan_sha256": sha(run_dir / "plan.json"),
            },
        )
    return {"run": run_dir.name, "results": results}


def selfcheck():
    Engine, convert, quality, request = product()
    raw = bytes((8 * 16000 + 17) * 2)
    assert [len(part) // 2 for _, part in chunks(raw, 4)] == [64000, 64000, 17]
    assert [len(part) // 2 for _, part in chunks(raw, 8)] == [128000, 17]
    assert b"".join(part for _, part in chunks(raw, 4)) == raw
    assert quality("가 나", "가 나") == {"cer": 0.0, "wer": 0.0}
    seen = {}

    class Fake:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            duration = len(audio) / 16000
            return [SimpleNamespace(text="검증", start=0.0, end=duration)], SimpleNamespace(
                duration=duration,
                duration_after_vad=duration,
            )

    engine = Engine(lambda _: None, device="cpu", compute_type="int8", cpu_threads=2)
    engine._model = Fake()
    assert engine._transcribe_chunk_sync(convert(bytes(16000 * 2)), request)[0].text == "검증"
    assert seen == {
        "language": "ko",
        "beam_size": 1,
        "condition_on_previous_text": False,
        "without_timestamps": False,
        "vad_filter": True,
        "initial_prompt": "ASR prototype",
        "hotwords": None,
    }
    return {
        "status": "passed",
        "classification": "fake_contract_only_not_model_inference",
        "checks": [
            "frozen_source",
            "chunk_tail_and_no_loss",
            "shared_metric",
            "actual_product_decode_kwargs",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("selfcheck", "prepare", "run", "arm"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--index", type=int)
    args = parser.parse_args()
    if args.mode == "arm":
        try:
            arm(args.plan, args.index)
        except BaseException as exc:
            write_new(
                args.plan.parent / f"arm-{args.index}-failure.json",
                {
                    "status": "failed",
                    "arm": args.index,
                    "source_commit": SOURCE,
                    "error_category": type(exc).__name__,
                    "at": now(),
                },
            )
            raise
    else:
        print(
            json.dumps(
                {"selfcheck": selfcheck, "prepare": prepare, "run": run}[args.mode](),
                ensure_ascii=False,
            ),
            flush=True,
        )
