from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import re
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .benchmark import normalize_text, percentile, quality_metrics
from .config import Settings, get_settings
from .correction import DeterministicCorrector
from .llm_correction import (
    ConstrainedCorrector,
    CorrectionError,
    OllamaConstrainedCorrector,
)
from .models import SessionContext

_NUMBER_PATTERN = re.compile(r"\d+(?:[.,:/-]\d+)*")


@dataclass(frozen=True)
class CorrectionFixture:
    fixture_id: str
    source_language: str
    source_text: str
    expected_text: str
    critical_tokens: tuple[str, ...]
    context: SessionContext


@dataclass(frozen=True)
class CorrectionBenchmarkOptions:
    fixtures_path: Path
    provider: str
    model: str | None
    repeats: int
    output_path: Path | None


def load_fixtures(path: Path) -> list[CorrectionFixture]:
    fixtures: list[CorrectionFixture] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on fixture line {line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"fixture line {line_number} must contain a JSON object")

        fixture_id = str(payload.get("id", "")).strip()
        source_language = str(payload.get("source_language", "")).strip()
        source_text = str(payload.get("source_text", "")).strip()
        expected_text = str(payload.get("expected_text", "")).strip()
        if not all([fixture_id, source_language, source_text, expected_text]):
            raise ValueError(
                f"fixture line {line_number} requires id, source_language, source_text, "
                "and expected_text"
            )
        if fixture_id in seen_ids:
            raise ValueError(f"duplicate fixture id: {fixture_id}")

        raw_tokens = payload.get("critical_tokens", [])
        if not isinstance(raw_tokens, list):
            raise ValueError(f"fixture {fixture_id} critical_tokens must be a list")
        critical_tokens = tuple(
            dict.fromkeys(str(token).strip() for token in raw_tokens if str(token).strip())
        )

        raw_context = payload.get("context", {})
        if raw_context is None:
            raw_context = {}
        if not isinstance(raw_context, dict):
            raise ValueError(f"fixture {fixture_id} context must be an object")

        fixtures.append(
            CorrectionFixture(
                fixture_id=fixture_id,
                source_language=source_language,
                source_text=source_text,
                expected_text=expected_text,
                critical_tokens=critical_tokens,
                context=SessionContext.model_validate(raw_context),
            )
        )
        seen_ids.add(fixture_id)

    if not fixtures:
        raise ValueError("correction benchmark requires at least one fixture")
    return fixtures


def build_llm_corrector(
    provider: str,
    model: str | None,
    settings: Settings,
) -> ConstrainedCorrector | None:
    if provider == "none":
        return None
    if provider == "ollama":
        return OllamaConstrainedCorrector(
            base_url=settings.ollama_url,
            model=model or settings.ollama_correction_model,
            request_timeout_seconds=max(settings.correction_timeout_seconds, 0.1),
        )
    raise ValueError("correction benchmark provider must be none or ollama")


def critical_token_metrics(
    critical_tokens: tuple[str, ...],
    output: str,
) -> dict[str, Any]:
    if not critical_tokens:
        return {"expected": 0, "matched": 0, "accuracy": None, "missing": []}
    normalized_output = normalize_text(output)
    missing = [
        token
        for token in critical_tokens
        if normalize_text(token) not in normalized_output
    ]
    matched = len(critical_tokens) - len(missing)
    return {
        "expected": len(critical_tokens),
        "matched": matched,
        "accuracy": round(matched / len(critical_tokens), 4),
        "missing": missing,
    }


def numeric_token_metrics(expected: str, output: str) -> dict[str, Any]:
    expected_tokens = _NUMBER_PATTERN.findall(expected)
    output_tokens = _NUMBER_PATTERN.findall(output)
    if not expected_tokens:
        return {
            "expected": [],
            "actual": output_tokens,
            "match": None,
        }
    return {
        "expected": expected_tokens,
        "actual": output_tokens,
        "match": expected_tokens == output_tokens,
    }


def classification_metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def _environment() -> dict[str, str | None]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine() or None,
        "processor": platform.processor() or None,
    }


def _fixture_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean_optional(values: list[float | None], digits: int = 4) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(statistics.fmean(present), digits)


async def _correct_once(
    fixture: CorrectionFixture,
    *,
    deterministic_corrector: DeterministicCorrector,
    llm_corrector: ConstrainedCorrector | None,
    llm_available: bool,
    unavailable_reason: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    deterministic = deterministic_corrector.correct(fixture.source_text, fixture.context)
    method = "deterministic"
    llm_attempted = False
    llm_applied = False
    fallback_reason: str | None = None
    output = deterministic

    if llm_corrector is not None:
        if not llm_available:
            method = "fallback"
            fallback_reason = unavailable_reason or "correction provider unavailable"
        else:
            llm_attempted = True
            try:
                output = await asyncio.wait_for(
                    llm_corrector.correct(
                        deterministic,
                        source_language=fixture.source_language,
                        context=fixture.context,
                    ),
                    timeout=max(timeout_seconds, 0.1),
                )
                method = "llm"
                llm_applied = True
            except TimeoutError:
                method = "fallback"
                fallback_reason = f"correction timed out after {timeout_seconds:.1f}s"
            except CorrectionError as exc:
                method = "fallback"
                fallback_reason = str(exc)

    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
    source_normalized = normalize_text(fixture.source_text)
    expected_normalized = normalize_text(fixture.expected_text)
    output_normalized = normalize_text(output)
    needs_change = source_normalized != expected_normalized
    predicted_change = source_normalized != output_normalized
    exact_correct = output_normalized == expected_normalized
    harmful_change = not needs_change and predicted_change
    missed_correction = needs_change and not predicted_change
    wrong_change = needs_change and predicted_change and not exact_correct
    critical = critical_token_metrics(fixture.critical_tokens, output)
    numeric = numeric_token_metrics(fixture.expected_text, output)
    quality = quality_metrics(fixture.expected_text, output)

    return {
        "status": "ok",
        "method": method,
        "latency_ms": latency_ms,
        "source_text": fixture.source_text,
        "deterministic_text": deterministic,
        "output_text": output,
        "expected_text": fixture.expected_text,
        "needs_change": needs_change,
        "predicted_change": predicted_change,
        "exact_correct": exact_correct,
        "harmful_change": harmful_change,
        "missed_correction": missed_correction,
        "wrong_change": wrong_change,
        "deterministic_changed": normalize_text(deterministic) != source_normalized,
        "llm_attempted": llm_attempted,
        "llm_applied": llm_applied,
        "fallback_reason": fallback_reason,
        "cer": quality["cer"],
        "wer": quality["wer"],
        "critical_tokens": critical,
        "numeric_tokens": numeric,
    }


async def run_correction_benchmark(
    options: CorrectionBenchmarkOptions,
    *,
    llm_corrector: ConstrainedCorrector | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    if options.repeats < 1:
        raise ValueError("benchmark repeats must be at least 1")
    fixtures = load_fixtures(options.fixtures_path)
    fixture_sha256 = _fixture_digest(options.fixtures_path)
    active_settings = settings or get_settings()
    deterministic_corrector = DeterministicCorrector()
    active_llm = (
        llm_corrector
        if llm_corrector is not None
        else build_llm_corrector(options.provider, options.model, active_settings)
    )
    llm_available = active_llm is not None
    unavailable_reason: str | None = None
    prepare_ms: float | None = None

    if active_llm is not None:
        prepare_started = time.perf_counter()
        try:
            await active_llm.prepare()
        except CorrectionError as exc:
            llm_available = False
            unavailable_reason = str(exc)
        prepare_ms = round((time.perf_counter() - prepare_started) * 1000.0, 1)

    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    cer_values: list[float | None] = []
    wer_values: list[float | None] = []
    exact_correct_count = 0
    harmful_change_count = 0
    missed_correction_count = 0
    wrong_change_count = 0
    critical_expected = 0
    critical_matched = 0
    numeric_cases = 0
    numeric_matches = 0
    llm_attempted_count = 0
    llm_applied_count = 0
    fallback_count = 0
    tp = fp = fn = tn = 0

    try:
        for fixture in fixtures:
            runs: list[dict[str, Any]] = []
            for repeat_index in range(options.repeats):
                run = await _correct_once(
                    fixture,
                    deterministic_corrector=deterministic_corrector,
                    llm_corrector=active_llm,
                    llm_available=llm_available,
                    unavailable_reason=unavailable_reason,
                    timeout_seconds=active_settings.correction_timeout_seconds,
                )
                run["repeat"] = repeat_index + 1
                runs.append(run)
                latencies.append(run["latency_ms"])
                cer_values.append(run["cer"])
                wer_values.append(run["wer"])
                exact_correct_count += int(run["exact_correct"])
                harmful_change_count += int(run["harmful_change"])
                missed_correction_count += int(run["missed_correction"])
                wrong_change_count += int(run["wrong_change"])
                llm_attempted_count += int(run["llm_attempted"])
                llm_applied_count += int(run["llm_applied"])
                fallback_count += int(run["method"] == "fallback")

                if run["needs_change"] and run["predicted_change"]:
                    tp += 1
                elif not run["needs_change"] and run["predicted_change"]:
                    fp += 1
                elif run["needs_change"] and not run["predicted_change"]:
                    fn += 1
                else:
                    tn += 1

                critical_expected += run["critical_tokens"]["expected"]
                critical_matched += run["critical_tokens"]["matched"]
                if run["numeric_tokens"]["match"] is not None:
                    numeric_cases += 1
                    numeric_matches += int(run["numeric_tokens"]["match"])

            results.append(
                {
                    "id": fixture.fixture_id,
                    "source_language": fixture.source_language,
                    "critical_tokens": list(fixture.critical_tokens),
                    "runs": runs,
                }
            )
    finally:
        if active_llm is not None:
            await active_llm.close()

    total_runs = len(fixtures) * options.repeats
    needs_change_runs = tp + fn
    already_correct_runs = fp + tn
    change_detection = classification_metrics(tp, fp, fn, tn)
    report = {
        "schema_version": 1,
        "status": "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": _environment(),
        "provider": options.provider,
        "model": getattr(active_llm, "model", options.model),
        "provider_available": llm_available if active_llm is not None else True,
        "provider_error": unavailable_reason,
        "config": {
            "fixtures_path": str(options.fixtures_path),
            "fixture_sha256": fixture_sha256,
            "fixture_count": len(fixtures),
            "repeats": options.repeats,
            "total_runs": total_runs,
        },
        "summary": {
            "prepare_ms": prepare_ms,
            "latency_mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "latency_max_ms": round(max(latencies), 1) if latencies else None,
            "exact_correct_rate": round(exact_correct_count / total_runs, 4),
            "cer_mean": _mean_optional(cer_values),
            "wer_mean": _mean_optional(wer_values),
            "change_detection": change_detection,
            "harmful_change_rate": (
                round(harmful_change_count / already_correct_runs, 4)
                if already_correct_runs
                else None
            ),
            "harmful_change_count": harmful_change_count,
            "missed_correction_rate": (
                round(missed_correction_count / needs_change_runs, 4)
                if needs_change_runs
                else None
            ),
            "missed_correction_count": missed_correction_count,
            "wrong_change_rate": (
                round(wrong_change_count / needs_change_runs, 4)
                if needs_change_runs
                else None
            ),
            "wrong_change_count": wrong_change_count,
            "critical_token_accuracy": (
                round(critical_matched / critical_expected, 4)
                if critical_expected
                else None
            ),
            "critical_tokens_matched": critical_matched,
            "critical_tokens_expected": critical_expected,
            "numeric_token_accuracy": (
                round(numeric_matches / numeric_cases, 4) if numeric_cases else None
            ),
            "numeric_cases": numeric_cases,
            "llm_attempted_count": llm_attempted_count,
            "llm_applied_count": llm_applied_count,
            "fallback_count": fallback_count,
        },
        "fixtures": results,
    }
    if options.output_path is not None:
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        options.output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def parse_args(argv: list[str] | None = None) -> CorrectionBenchmarkOptions:
    parser = argparse.ArgumentParser(
        description="Benchmark LangTextFlow transcript correction quality with JSONL fixtures"
    )
    parser.add_argument("fixtures", type=Path, help="JSONL correction fixture file")
    parser.add_argument("--provider", choices=["none", "ollama"], default="none")
    parser.add_argument("--model", default=None, help="Ollama correction model name")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    return CorrectionBenchmarkOptions(
        fixtures_path=args.fixtures,
        provider=args.provider,
        model=args.model,
        repeats=args.repeats,
        output_path=args.output,
    )


async def _async_main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    report = await run_correction_benchmark(options)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
