"""Opt-in, local-only synthetic Ming TTS smoke; never a caption/quality benchmark.

Only an already prepared, pinned source checkout and local model directory are
accepted. No prompt recording, API credential, download or cloud fallback is used.
Native execution requires Linux syscall network isolation before model imports.
The Python socket audit guard is defense in depth, not an OS/network packet audit.
Use an external process timeout; an interrupted run retains an incomplete report.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MODEL_ID = "inclusionAI/Ming-3.0-tts-0.5B"
MODEL_REVISION = "7247f4c94a5b2e987404ed28a5cfec41b1a4ca61"
SOURCE_REVISION = "a39b80660bdc5a4394d5e1d41af6dbfd9c443a54"
EXPECTED_SHA256 = {
    "model.safetensors": "45c4d78d8ad6b1d942e3da86ba035fece02de569f7b2ca7e9d121dfaf18d674c",
    "campplus.onnx": "a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73",
}
REQUIRED_MODEL_FILES = (
    "config.json", "tokenizer.json", "tokenizer_config.json",
    "model.safetensors", "campplus.onnx",
)
REQUIRED_SOURCE_FILES = (
    "ming_tts.py", "tts_tasks.py", "modeling_bailingmm.py",
    "configuration_bailingmm.py", "spkemb_extractor.py",
)
FIXTURES = {
    "en": "Welcome. This is a local speech test.",
    "ko": "안녕하세요. 로컬 음성 시험입니다.",
    "ja": "こんにちは。ローカル音声のテストです。",
}
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
    "ORT_DISABLE_TELEMETRY": "1",
}


class OutgoingNetworkDenied(RuntimeError):
    pass


class PythonSocketGuard:
    """Deny audited Python DNS/connection/send attempts; never inspect payloads."""

    EVENTS = frozenset({
        "socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
        "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr",
    })

    def __init__(self) -> None:
        self.enabled = False
        self.installed = False
        self.attempts: dict[str, int] = {}

    def _audit(self, event: str, _args: tuple[Any, ...]) -> None:
        if self.enabled and event in self.EVENTS:
            self.attempts[event] = self.attempts.get(event, 0) + 1
            raise OutgoingNetworkDenied(f"local smoke blocked Python event: {event}")

    def __enter__(self) -> PythonSocketGuard:
        sys.addaudithook(self._audit)
        self.installed = True
        self.enabled = True
        return self

    def __exit__(self, *_: object) -> None:
        # CPython audit hooks cannot be removed. This inactive hook becomes a no-op.
        self.enabled = False

    def report(self) -> dict[str, Any]:
        return {
            "python_socket_guard_installed": self.installed,
            "denied_attempt_count": sum(self.attempts.values()),
            "denied_events": dict(self.attempts),
            "scope": "audited Python DNS/connect/send events during import/load/synthesis",
            "os_packet_audit_performed": False,
            "native_or_subprocess_egress_audited": False,
        }


@contextlib.contextmanager
def offline_environment(threads: int):
    values = {**OFFLINE_ENV, "OMP_NUM_THREADS": str(threads), "MKL_NUM_THREADS": str(threads)}
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_directory(value: str, label: str) -> Path:
    if "://" in value or value.startswith(("\\\\", "//")):
        raise ValueError(f"{label} must be an absolute local directory, never a URL/model ID")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute local directory, never a model ID")
    if not path.is_dir():
        raise ValueError(f"{label} local directory is missing")
    return path.resolve()


def validate_assets(source_value: str, model_value: str) -> dict[str, Any]:
    source = local_directory(source_value, "source-dir")
    model = local_directory(model_value, "model-path")
    for root, names in ((source, REQUIRED_SOURCE_FILES), (model, REQUIRED_MODEL_FILES)):
        missing = [name for name in names if not (root / name).is_file()
                   or (root / name).stat().st_size == 0]
        if missing:
            raise ValueError(f"missing local assets in {root.name}: {', '.join(missing)}")
    if (model / "model.safetensors").stat().st_size != 2959055504:
        raise ValueError("model.safetensors size differs from the pinned checkpoint")
    git = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    if git.stdout.strip() != SOURCE_REVISION:
        raise ValueError("source checkout does not match the pinned source revision")
    dirty = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    if dirty.stdout.strip():
        raise ValueError("source checkout has tracked modifications; use the clean pinned source")
    files = {}
    for name in REQUIRED_MODEL_FILES:
        digest = sha256_file(model / name)
        expected = EXPECTED_SHA256.get(name)
        if expected is not None and digest != expected:
            raise ValueError(f"{name} SHA256 differs from the pinned checkpoint")
        files[name] = {
            "sha256": digest, "bytes": (model / name).stat().st_size,
            "verified_against_expected_sha256": expected is not None,
        }
    return {
        "source_dir": str(source), "model_path": str(model),
        "source_revision": git.stdout.strip(), "tracked_source_clean": True,
        "source_files_sha256": {name: sha256_file(source / name) for name in REQUIRED_SOURCE_FILES},
        "model_files": files,
        "identity_scope": "weights and campplus verified; tokenizer/config hashes recorded only",
    }


def rss_peak() -> dict[str, Any]:
    result: dict[str, Any] = {
        "method": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
        "scope": "process lifetime high water; excludes children and GPU/device memory",
        "os": platform.system(), "peak_mib": None,
    }
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if platform.system() == "Darwin":
            result.update(raw_value=raw, raw_unit="bytes", peak_mib=raw / (1024 ** 2))
        elif platform.system() == "Linux":
            result.update(raw_value=raw, raw_unit="KiB", peak_mib=raw / 1024)
        else:
            result["unavailable_reason"] = "ru_maxrss units not normalized for this OS"
    except (ImportError, OSError) as exc:
        result["unavailable_reason"] = type(exc).__name__
    return result


def audio_statistics(samples: list[float], sample_rate: int) -> dict[str, Any]:
    if not isinstance(sample_rate, int) or not 8000 <= sample_rate <= 192000:
        raise ValueError("invalid generated audio sample rate")
    if len(samples) > sample_rate * 120:
        raise ValueError("generated audio exceeds the 120-second synthetic smoke bound")
    finite = all(math.isfinite(value) for value in samples)
    nonzero = bool(samples) and finite and any(value != 0 for value in samples)
    clipped = sum(abs(value) > 1 for value in samples) if finite else None
    return {
        "sample_count": len(samples), "sample_rate": sample_rate,
        "audio_duration_seconds": len(samples) / sample_rate,
        "finite": finite, "nonzero": nonzero,
        "absolute_peak": max((abs(value) for value in samples), default=0) if finite else None,
        "rms": math.sqrt(sum(value * value for value in samples) / len(samples))
        if finite and samples else None,
        "samples_outside_pcm_unit_range": clipped,
        "pcm_clipping_fraction": (
            clipped / len(samples) if samples and clipped is not None else None
        ),
    }


def write_wav(path: Path, samples: list[float], sample_rate: int) -> dict[str, Any]:
    pcm = array("h", (max(-32768, min(32767, round(value * 32768))) for value in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    raw = pcm.tobytes()
    with path.open("xb") as target, wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(raw)
    return {
        "wav_file": path.name, "wav_sha256": sha256_file(path),
        "pcm16_sha256": hashlib.sha256(raw).hexdigest(),
        "format": "mono PCM16 WAV; clamp only, no loudness normalization",
    }


def fixture_kwargs(language: str, voice: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "text": FIXTURES[language], "language_id": language,
        "raw_text": True, "use_spk_emb": False, "use_zero_spk_emb": True,
    }
    if language == "en":
        result.update(task="IP", controls={"ip": voice}, instruction_schema="en")
    else:
        # Upstream MULTILINGUAL aliases TTS and explicitly accepts a zero speaker.
        result.update(task="MULTILINGUAL", use_zero_spk_emb=True)
    return result


@contextlib.contextmanager
def source_import_path(source: Path):
    sys.path.insert(0, str(source))
    try:
        yield
    finally:
        sys.path.remove(str(source))


def install_native_network_guard() -> dict[str, Any]:
    """Load the repository's local helper; do not import any model dependency."""
    if platform.system() != "Linux":
        raise RuntimeError("this smoke requires Linux native network isolation before model import")
    helper = Path(__file__).resolve().with_name("local_model_network_guard.py")
    spec = importlib.util.spec_from_file_location("ltf_local_model_network_guard", helper)
    if spec is None or spec.loader is None:
        raise RuntimeError("local native network isolation helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.install_kernel_network_guard()
    if not isinstance(result, dict) or result.get("installed") is not True:
        raise RuntimeError("native network isolation was not installed; refusing model import")
    return {**result, "helper_sha256": sha256_file(helper)}


def load_dependencies(source: Path):
    """The only heavy imports; never reached without opt-in and valid local assets."""
    import onnxruntime

    # ORT_DISABLE_TELEMETRY is already set before this import. The API is an
    # additional layer; by itself it can be too late for ORT initialization events.
    onnxruntime.disable_telemetry_events()
    import ming_tts
    import torch

    if Path(ming_tts.__file__).resolve() != source / "ming_tts.py":
        raise ValueError("ming_tts was imported from a different source checkout")
    return torch, ming_tts.MingTTS


def synchronize(torch, device: str) -> None:
    if device == "cuda":
        torch.cuda.synchronize()
    elif device == "mps":
        torch.mps.synchronize()


def write_report(output: Path, report: dict[str, Any]) -> None:
    report["memory"] = rss_peak()
    report["completed_case_count"] = sum(
        row["status"] == "completed_waveform_smoke" for row in report["cases"]
    )
    report["failed_case_count"] = sum(row["status"] == "failed" for row in report["cases"])
    data = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix="report-", suffix=".tmp", dir=output)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
        os.replace(temporary, output / "report.json")
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(options: argparse.Namespace) -> tuple[dict[str, Any], int]:
    output = Path(options.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Preserve earlier evidence, including incomplete/failed runs.
    with (output / "report.json").open("x", encoding="utf-8"):
        pass
    selected = options.case or list(FIXTURES)
    report: dict[str, Any] = {
        "schema_version": 1, "status": "not_exercised", "phase": "preflight",
        "started_at": datetime.now(UTC).isoformat(),
        "model_id": MODEL_ID, "expected_model_revision": MODEL_REVISION,
        "expected_source_revision": SOURCE_REVISION,
        "actual_model_execution": False, "model_load_attempted": False,
        "inference_attempted": False,
        "actual_model_execution_definition": "synthesize invoked; completion reported separately",
        "runner_sha256_at_start": sha256_file(Path(__file__)),
        "field_ready": False, "semantic_quality": "not_evaluated",
        "measurement_scope": "synthetic TTS waveform smoke; no ASR/MT/caption qualification",
        "first_audio_latency_seconds": None,
        "first_audio_latency_reason": "synchronous whole-waveform API; first chunk not observed",
        "python": platform.python_version(), "platform": platform.platform(),
        "settings": {
            "device": options.device, "cpu_autocast": options.cpu_autocast,
            "seed": options.seed, "torch_threads": options.threads,
            "torch_interop_threads": 1, "upstream_onnx_intra_op_threads": 2,
            "max_decode_steps": options.max_decode_steps,
            "text_normalization": False, "raw_text": True,
            "cfg": 2.0, "sigma": 0.25, "temperature": 0.0,
            "reference_audio_used": False,
        },
        "cases": [{"language": language, "text": FIXTURES[language],
                   "status": "not_exercised"} for language in selected],
        "model_load_seconds": None, "dependency_import_seconds": None,
        "network": {"python_socket_guard_installed": False,
                    "os_packet_audit_performed": False},
        "native_network_isolation": {"installed": False, "required_before_model_import": True},
        "onnxruntime_telemetry": {
            "disable_environment_requested": True,
            "disable_api_called_successfully": False,
        },
        "offline_environment_during_execution": OFFLINE_ENV,
    }
    guard = PythonSocketGuard()
    try:
        if not 1 <= options.threads <= 16 or not 1 <= options.max_decode_steps <= 256:
            raise ValueError("threads must be 1-16 and max-decode-steps must be 1-256")
        if not 0 <= options.seed <= 2 ** 32 - 1 or len(set(selected)) != len(selected):
            raise ValueError("invalid seed or duplicate cases")
        if options.cpu_autocast and options.device != "cpu":
            raise ValueError("--cpu-autocast applies only to --device cpu")
        if not options.allow_model_execution:
            report["reason"] = "explicit --allow-model-execution was not supplied"
            report["assets_checked"] = False
            write_report(output, report)
            return report, 0
        report["status"] = "incomplete"
        report["phase"] = "asset_validation"
        write_report(output, report)
        report["assets"] = validate_assets(options.source_dir, options.model_path)
        report["phase"] = "native_network_isolation"
        write_report(output, report)
        report["native_network_isolation"] = install_native_network_guard()
        report["phase"] = "dependency_import"
        write_report(output, report)
        with offline_environment(options.threads), guard, source_import_path(
            Path(report["assets"]["source_dir"])
        ):
            started = time.perf_counter()
            torch, constructor = load_dependencies(Path(report["assets"]["source_dir"]))
            report["onnxruntime_telemetry"]["disable_api_called_successfully"] = True
            report["dependency_import_seconds"] = time.perf_counter() - started
            report["dependencies"] = {}
            for name in ("torch", "torchaudio", "transformers", "onnxruntime"):
                try:
                    report["dependencies"][name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    report["dependencies"][name] = "unavailable"
            torch.set_num_threads(options.threads)
            torch.set_num_interop_threads(1)
            random.seed(options.seed)
            torch.manual_seed(options.seed)
            report["phase"] = "model_load"
            report["model_load_attempted"] = True
            write_report(output, report)
            started = time.perf_counter()
            model = constructor(
                report["assets"]["model_path"], device=options.device,
                text_normalization=False,
            )
            synchronize(torch, options.device)
            report["model_load_seconds"] = time.perf_counter() - started
            for row in report["cases"]:
                report["phase"] = f"synthesis:{row['language']}"
                row["status"] = "incomplete"
                row["request"] = fixture_kwargs(row["language"], options.english_voice)
                report["actual_model_execution"] = True
                report["inference_attempted"] = True
                row["inference_attempted"] = True
                write_report(output, report)
                random.seed(options.seed)
                torch.manual_seed(options.seed)
                synchronize(torch, options.device)
                started = time.perf_counter()
                mode = torch.autocast("cpu", dtype=torch.bfloat16) if options.cpu_autocast \
                    else contextlib.nullcontext()
                try:
                    with mode:
                        waveform = model.synthesize(
                            **row["request"], max_decode_steps=options.max_decode_steps,
                        )
                    synchronize(torch, options.device)
                    row["generation_seconds"] = time.perf_counter() - started
                    if waveform.ndim not in {1, 2} or (
                        waveform.ndim == 2 and waveform.shape[0] != 1
                    ):
                        raise ValueError("expected a mono generated waveform")
                    samples = waveform.detach().cpu().float().reshape(-1).tolist()
                    row["audio"] = audio_statistics(samples, int(model.sample_rate))
                    if not row["audio"]["finite"] or not row["audio"]["nonzero"]:
                        raise ValueError(
                            "generated waveform is empty, nonfinite or entirely silent"
                        )
                    row["rtf"] = (
                        row["generation_seconds"] / row["audio"]["audio_duration_seconds"]
                    )
                    row.update(write_wav(output / f"synthetic-{row['language']}.wav",
                                         samples, int(model.sample_rate)))
                    row["status"] = "completed_waveform_smoke"
                    row["semantic_quality"] = "not_evaluated"
                    row["utterance_completeness"] = "not_evaluated"
                    row["end_of_sequence_observed"] = None
                    row["decode_cap_reached"] = None
                    row["first_audio_latency_seconds"] = None
                    row["memory"] = rss_peak()
                except Exception as exc:
                    row["status"] = "failed"
                    row["error"] = {
                        "type": type(exc).__name__, "message": str(exc)[:2000],
                    }
                    row["attempt_wall_seconds"] = time.perf_counter() - started
                    raise
                report["network"] = guard.report()
                write_report(output, report)
            if guard.attempts:
                raise OutgoingNetworkDenied(
                    "a dependency attempted network access during the smoke"
                )
        report["status"] = "completed_waveform_smoke"
        report["phase"] = "finished"
        exit_code = 0
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        exit_code = 1
    if report["phase"] != "preflight":
        report["network"] = guard.report()
    report["finished_at"] = datetime.now(UTC).isoformat()
    write_report(output, report)
    return report, exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-model-execution", action="store_true")
    parser.add_argument("--case", action="append", choices=tuple(FIXTURES))
    parser.add_argument("--english-voice", choices=("US_Emma", "UK_George"), default="US_Emma")
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--cpu-autocast", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--max-decode-steps", type=int, default=256)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        report, code = run(options)
    except (OSError, ValueError) as exc:
        # Cannot safely write over an earlier report or into an invalid destination.
        print(json.dumps({"status": "failed", "phase": "report_destination",
                          "error": type(exc).__name__, "field_ready": False}))
        return 1
    print(json.dumps({
        "status": report["status"], "phase": report["phase"],
        "report": str(Path(options.output_dir).expanduser().resolve() / "report.json"),
        "field_ready": False,
    }))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
