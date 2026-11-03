"""One cached CPU MT candidate on the existing fixed 16-case KO→EN scorer.

Evaluation glue only. No model downloads, production provider registration or
field-quality claims. POSIX supervision kills the candidate's whole process group.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/server"))

from langtextflow.models import (  # noqa: E402
    MAX_TRANSLATION_TEXT_CHARS,
    ProductPreset,
    SessionContext,
)
from langtextflow.prompt_safety import bounded_model_text  # noqa: E402
from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator  # noqa: E402
from langtextflow.translation.ollama import OllamaTranslator  # noqa: E402
from scripts.compare_church_cpu_pipeline import (  # noqa: E402
    CTranslateMarian,
    text_benchmark,
    validate_fixtures,
)

FIXTURE = ROOT / "benchmarks/church_translation_quality.v1.jsonl"
FIXTURE_SHA256 = "1b3560e9a86590e84db4d159ac2116a72ad7536e945c5a61c8f0664cc65e9688"
MODELS = {"opus-small": "Helsinki-NLP/opus-mt-ko-en", "m2m100": "facebook/m2m100_418M"}
CONTEXT = SessionContext(title="Synthetic church service evaluation", preset=ProductPreset.CHURCH)


def digest_value(value):
    match = re.fullmatch(r"(?:sha256:)?([0-9a-f]{64})", value or "")
    return "sha256:" + match[1] if match else None


class CachedTranslateGemma(OllamaTranslator):
    """Reuse the product prompt/validator, with explicit CPU and decoding limits."""

    def __init__(self, args):
        super().__init__(base_url=args.ollama_url, model=args.model,
                         request_timeout_seconds=args.timeout)
        self.expected_digest = digest_value(args.expected_digest)
        if self.expected_digest is None:
            raise ValueError("exact installed Ollama digest is required")
        self.digest = None
        self.options = {"temperature": 0, "seed": 0, "num_predict": 256,
                        "num_gpu": 0, "num_thread": args.threads}

    async def _request(self, method, route, **kwargs):
        import httpx

        from langtextflow.provider_http import bounded_json_request

        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            return await bounded_json_request(client, method, self.base_url + route, **kwargs)

    async def check_identity(self):
        payload = await self._request("GET", "/api/tags")
        matches = [item for item in payload.get("models", [])
                   if item.get("name", item.get("model")) == self.model]
        if len(matches) != 1 or digest_value(matches[0].get("digest")) != self.expected_digest:
            raise FileNotFoundError("exact cached Ollama tag/digest unavailable")
        self.digest = digest_value(matches[0]["digest"])

    async def prepare(self):
        await self.check_identity()
        # Load the cached model before scored text, just as OPUS/M2M100 do.
        loaded = await self._request("POST", "/api/generate", json={
            "model": self.model, "prompt": "", "stream": False,
            "options": self.options, "keep_alive": "10m",
        })
        if loaded.get("done") is not True:
            raise RuntimeError("cached Ollama model load did not complete")

    async def translate(self, text, *, source_language, target_language, context):
        if (source_language, target_language) != ("ko", "en") or context.glossary:
            raise ValueError("fixed KO→EN glossary-off evaluation only")
        payload = await self._request("POST", "/api/chat", json={
            "model": self.model, "messages": self.build_translategemma_messages(
                text, source_language=source_language, target_language=target_language,
                context=context,
            ), "stream": False, "options": self.options,
        })
        return self.validate_caption_text(bounded_model_text(
            payload["message"]["content"], label="Ollama translation",
        ))

    async def close(self):
        if self.digest is not None:
            # Use a dedicated, caller-owned Ollama server; release this benchmark model.
            await self._request("POST", "/api/generate", json={
                "model": self.model, "keep_alive": 0,
            })


def build_provider(args):
    if args.candidate == "ollama":
        return CachedTranslateGemma(args)
    if not args.ct2_dir or not args.tokenizer_dir:
        raise FileNotFoundError("local caches not configured")
    if not all((args.ct2_dir / name).is_file() for name in ("model.bin", "config.json")):
        raise FileNotFoundError("local CT2 cache incomplete")
    if not args.tokenizer_dir.is_dir():
        raise FileNotFoundError("local tokenizer cache missing")
    if args.candidate == "opus-small":
        return CTranslateMarian(args.ct2_dir, args.tokenizer_dir, threads=args.threads)
    return M2M100CT2Translator(model_dir=str(args.ct2_dir),
                              tokenizer_dir=str(args.tokenizer_dir), cpu_threads=args.threads)


class ScorerAdapter:
    def __init__(self, provider, runner, timeout):
        self.provider, self.runner, self.timeout = provider, runner, timeout
        self.outcomes = []

    def run(self, coroutine):
        async def bounded():
            async with asyncio.timeout(self.timeout):
                return await coroutine
        return self.runner.run(bounded())

    def translate(self, text):
        try:
            result = (self.provider.translate(text) if isinstance(self.provider, CTranslateMarian)
                      else self.run(self.provider.translate(
                          text, source_language="ko", target_language="en", context=CONTEXT,
                      )))
            if not isinstance(result, str) or len(result) > MAX_TRANSLATION_TEXT_CHARS:
                raise TypeError("provider returned non-text")
            self.outcomes.append({"status": "measured", "error_type": None})
            return result
        except Exception as exc:
            self.outcomes.append({
                "status": "timed_out" if isinstance(exc, TimeoutError) else "failed",
                "error_type": type(exc).__name__,
            })
            # Existing scorer counts these as invalid and includes all expected terms.
            return ""


def base_report(args):
    rows, digest = validate_fixtures(args.fixture)
    if digest != FIXTURE_SHA256:
        raise ValueError("fixture bytes differ from the fixed comparison corpus")
    model = args.model if args.candidate == "ollama" else MODELS[args.candidate]
    inputs = [{"id": row["id"], "sha256": hashlib.sha256(
        row["source_text"].encode("utf-8")).hexdigest()} for row in rows]
    return {
        "schema_version": 1, "status": "not_started", "candidate": args.candidate,
        "fixture_sha256": digest, "source_language": "ko", "target_language": "en",
        "inputs": inputs,
        "requested_model": model, "configured_model": model, "verified_model": None,
        "requested_revision": args.requested_revision, "model_revision": None,
        "runtime_reported_digest": None, "cpu_threads": args.threads,
        "runtime_reported_digest_kind": "ollama_manifest_tag_digest" if args.candidate == "ollama"
        else None,
        "startup_policy": "cached load and file fingerprinting before scoring; no text warmup",
        "glossary_policy": "off", "context": CONTEXT.model_dump(mode="json"),
        "decoding": ({"temperature": 0, "seed": 0, "num_predict": 256,
                      "num_gpu": 0, "num_thread": args.threads} if args.candidate == "ollama"
                     else {"beam_size": 1, "max_decoding_length": 256, "compute_type": "int8"}),
        "metric_population": {
            "chrf": "mean sentence chrF++ over valid outputs only; not corpus chrF",
            "terms": "all 16 cases; failed/invalid cases remain in denominator",
            "latency": "all 16 calls, including failures; startup/cleanup excluded",
        },
        "resource_scope": "translation-loop process tree; external Ollama server excluded",
        "case_outcomes": [dict(row, status="not_observed", error_type=None) for row in inputs],
        "recorded_cases": 0, "comparison_eligible": False, "valid_output_coverage": None,
        "unobserved_cases": 16,
        "implementation_sha256": {
            "cli": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scorer": hashlib.sha256((ROOT / "scripts/compare_church_cpu_pipeline.py")
                                     .read_bytes()).hexdigest(),
        },
        "quality_certified": False, "field_ready": False,
    }


def evaluate(args):
    report = base_report(args)
    provider = None
    started = time.perf_counter()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    with asyncio.Runner() as runner:
        phase = "prepare"
        try:
            report["local_files_sha256"] = {}
            for label, directory in (("ct2", args.ct2_dir), ("tokenizer", args.tokenizer_dir)):
                if directory is None:
                    continue
                for name in ("model.bin", "config.json", "shared_vocabulary.json",
                             "source_vocabulary.json", "target_vocabulary.json", "vocab.json",
                             "tokenizer_config.json", "sentencepiece.bpe.model", "source.spm",
                             "target.spm"):
                    path = directory / name
                    if path.is_file():
                        with path.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        report["local_files_sha256"][f"{label}/{name}"] = digest
            provider = build_provider(args)
            adapter = ScorerAdapter(provider, runner, args.timeout)
            if not isinstance(provider, CTranslateMarian):
                adapter.run(provider.prepare())
            if isinstance(provider, CachedTranslateGemma):
                report["runtime_reported_digest"] = provider.digest
            report["startup_ms"] = round((time.perf_counter() - started) * 1000, 2)
            phase = "measure"
            report.update(text_benchmark(adapter, args.fixture))
            if report["fixture_sha256"] != FIXTURE_SHA256:
                raise ValueError("comparison fixture changed while loading the model")
            report["case_outcomes"] = [dict(inputs, **outcome) for inputs, outcome in
                                       zip(report["inputs"], adapter.outcomes, strict=True)]
            report["recorded_cases"] = len(adapter.outcomes)
            report["unobserved_cases"] = 16 - len(adapter.outcomes)
            report["valid_output_coverage"] = report["valid_outputs"] / 16
            report["execution_failures"] = sum(item["status"] != "measured"
                                                for item in adapter.outcomes)
            report["measured_invalid_outputs"] = sum(
                outcome["status"] == "measured" and not sample["valid"] for outcome, sample
                in zip(adapter.outcomes, report["samples"], strict=True)
            )
            failed = any(item["status"] != "measured" for item in adapter.outcomes)
            timed_out = any(item["status"] == "timed_out" for item in adapter.outcomes)
            report["status"] = "timed_out" if timed_out else "failed" if failed else (
                "measured" if report["valid_outputs"] == 16 else "invalid_outputs"
            )
            if isinstance(provider, CachedTranslateGemma):
                phase = "identity_check"
                adapter.run(provider.check_identity())
                report["runtime_reported_digest"] = provider.digest
        except Exception as exc:
            report.update(status="timed_out" if isinstance(exc, TimeoutError) else
                          "unavailable" if phase == "prepare" or isinstance(exc, ImportError) else
                          "failed", error_type=type(exc).__name__, failure_phase=phase)
            if phase == "prepare":
                for item in report["case_outcomes"]:
                    item["status"] = "not_attempted"
        finally:
            try:
                if provider is not None and not isinstance(provider, CTranslateMarian):
                    ScorerAdapter(provider, runner, args.timeout).run(provider.close())
                report["cleanup"] = "completed"
            except Exception as exc:
                report.update(status="failed", cleanup="failed",
                              cleanup_error_type=type(exc).__name__)
    report["comparison_eligible"] = (
        report["status"] == "measured" and report["recorded_cases"] == 16
    )
    return report


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def worker_command(args):
    command = [sys.executable, "-m", "scripts.compare_cached_mt_text", "--_worker"]
    for key in ("candidate", "fixture", "output", "model", "threads", "timeout",
                "total_timeout", "ollama_url", "expected_digest", "requested_revision",
                "ct2_dir", "tokenizer_dir"):
        value = getattr(args, key)
        if value is not None:
            command.extend(["--" + key.replace("_", "-"), str(value)])
    return command


def supervise(args):
    report = base_report(args)
    if os.name != "posix":
        report.update(status="unavailable", error_type="UnsupportedSupervisorPlatform")
        write_report(args.output, report)
        return report
    try:
        process = subprocess.Popen(
            worker_command(args),
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        report.update(status="unavailable", error_type="CandidateLaunchFailed")
        write_report(args.output, report)
        return report
    def interrupted(*_):
        raise KeyboardInterrupt

    previous_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        process.wait(timeout=args.total_timeout)
        try:
            report = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report.update(status="failed", error_type="CandidateReportInvalid")
        if report["status"] == "not_started":
            report.update(status="failed", error_type="CandidateReportIncomplete")
        if process.returncode != 0:
            report.update(status="failed", error_type="CandidateProcessFailed",
                          process_returncode=process.returncode, comparison_eligible=False)
    except subprocess.TimeoutExpired:
        report.update(status="timed_out", error_type="CandidateProcessTimeout")
    except KeyboardInterrupt:
        report.update(status="cancelled", error_type="Interrupted")
    finally:
        previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        # M2M100's native child inherits this group; never leave a benchmark model behind.
        if process.returncode is not None:
            try:
                os.killpg(process.pid, 0)
                report.update(status="failed", error_type="ResidualChildProcesses",
                              comparison_eligible=False)
            except ProcessLookupError:
                pass
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            report.update(status="failed", cleanup="reap_failed", error_type="CandidateReapTimeout",
                          comparison_eligible=False)
        finally:
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
    write_report(args.output, report)
    return report


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=[*MODELS, "ollama"], required=True)
    parser.add_argument("--ct2-dir", type=Path)
    parser.add_argument("--tokenizer-dir", type=Path)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--model", default="translategemma:4b")
    parser.add_argument("--requested-revision")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--expected-digest")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--total-timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    # Native product children must use this checkout even with a reused dependency venv.
    os.environ["PYTHONPATH"] = (
        str(ROOT / "apps/server") + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    if (not 1 <= args.threads <= 4 or not 1 <= args.timeout <= 120
            or not 30 <= args.total_timeout <= 1800):
        parser.error("threads/timeouts outside bounded evaluation limits")
    if args.requested_revision and not re.fullmatch(r"[0-9a-f]{40}", args.requested_revision):
        parser.error("requested revision must be an immutable 40-character SHA")
    if args.candidate == "ollama":
        endpoint = urlsplit(args.ollama_url)
        if (endpoint.scheme != "http" or endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or endpoint.path not in {"", "/"}):
            parser.error("a dedicated loopback-only Ollama endpoint is required")
        if not re.fullmatch(r"translategemma:[A-Za-z0-9_.-]{1,64}", args.model):
            parser.error("only an explicit TranslateGemma tag is eligible")
        if not digest_value(args.expected_digest):
            parser.error("the expected installed Ollama digest is required")
    args.output = args.output.resolve()
    args.fixture = args.fixture.resolve()
    for key in ("ct2_dir", "tokenizer_dir"):
        if value := getattr(args, key):
            setattr(args, key, value.resolve())
    if args._worker:
        write_report(args.output, evaluate(args))
        return 0
    write_report(args.output, base_report(args))
    report = supervise(args)
    print(json.dumps({key: report[key] for key in
                      ("candidate", "status", "fixture_sha256", "comparison_eligible")}, indent=2))
    return 0 if report["comparison_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
