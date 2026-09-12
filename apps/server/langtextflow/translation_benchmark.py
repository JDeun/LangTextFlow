from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .benchmark import normalize_text, percentile, quality_metrics
from .config import Settings, get_settings
from .models import GlossaryEntry, ProductPreset, SessionContext
from .translation import (
    OllamaTranslator,
    OpenAICompatibleTranslator,
    TranslationError,
    Translator,
)


@dataclass(frozen=True)
class TranslationFixture:
    fixture_id: str
    source_language: str
    target_language: str
    source_text: str
    reference_text: str
    expected_terms: tuple[str, ...]
    context: SessionContext


@dataclass(frozen=True)
class TranslationBenchmarkOptions:
    fixtures_path: Path
    provider: str
    model: str | None
    repeats: int
    output_path: Path | None


def load_fixtures(path: Path) -> list[TranslationFixture]:
    fixtures: list[TranslationFixture] = []
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
        target_language = str(payload.get("target_language", "")).strip()
        source_text = str(payload.get("source_text", "")).strip()
        reference_text = str(payload.get("reference_text", "")).strip()
        if not all([fixture_id, source_language, target_language, source_text, reference_text]):
            raise ValueError(
                f"fixture line {line_number} requires id, source_language, target_language, "
                "source_text, and reference_text"
            )
        if fixture_id in seen_ids:
            raise ValueError(f"duplicate fixture id: {fixture_id}")
        if source_language == target_language:
            raise ValueError(f"fixture {fixture_id} source and target languages must differ")

        raw_terms = payload.get("expected_terms", [])
        if not isinstance(raw_terms, list):
            raise ValueError(f"fixture {fixture_id} expected_terms must be a list")
        expected_terms = tuple(
            dict.fromkeys(str(term).strip() for term in raw_terms if str(term).strip())
        )

        raw_context = payload.get("context", {})
        if raw_context is None:
            raw_context = {}
        if not isinstance(raw_context, dict):
            raise ValueError(f"fixture {fixture_id} context must be an object")
        context = SessionContext.model_validate(raw_context)

        fixtures.append(
            TranslationFixture(
                fixture_id=fixture_id,
                source_language=source_language,
                target_language=target_language,
                source_text=source_text,
                reference_text=reference_text,
                expected_terms=expected_terms,
                context=context,
            )
        )
        seen_ids.add(fixture_id)

    if not fixtures:
        raise ValueError("translation benchmark requires at least one fixture")
    return fixtures


def build_translator(
    provider: str,
    model: str | None,
    settings: Settings,
) -> Translator:
    if provider == "ollama":
        return OllamaTranslator(
            base_url=settings.ollama_url,
            model=model or settings.ollama_translation_model,
        )
    if provider == "openai-compatible":
        resolved_model = model or settings.openai_compatible_translation_model
        if not resolved_model:
            raise ValueError("OpenAI-compatible benchmark requires --model or configured model")
        return OpenAICompatibleTranslator(
            base_url=settings.openai_compatible_url,
            model=resolved_model,
            api_key=settings.openai_compatible_api_key,
            request_timeout_seconds=max(settings.openai_compatible_timeout_seconds, 0.1),
        )
    raise ValueError("translation benchmark provider must be ollama or openai-compatible")


def terminology_metrics(expected_terms: tuple[str, ...], hypothesis: str) -> dict[str, Any]:
    if not expected_terms:
        return {
            "expected": 0,
            "matched": 0,
            "accuracy": None,
            "missing": [],
        }
    normalized_hypothesis = normalize_text(hypothesis)
    matched_terms: list[str] = []
    missing_terms: list[str] = []
    for term in expected_terms:
        if normalize_text(term) in normalized_hypothesis:
            matched_terms.append(term)
        else:
            missing_terms.append(term)
    return {
        "expected": len(expected_terms),
        "matched": len(matched_terms),
        "accuracy": round(len(matched_terms) / len(expected_terms), 4),
        "missing": missing_terms,
    }


def _mean_optional(values: list[float | None], digits: int = 4) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(statistics.fmean(present), digits)


async def run_translation_benchmark(
    options: TranslationBenchmarkOptions,
    *,
    translator: Translator | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    if options.repeats < 1:
        raise ValueError("benchmark repeats must be at least 1")
    fixtures = load_fixtures(options.fixtures_path)
    active_settings = settings or get_settings()
    active_translator = translator or build_translator(
        options.provider,
        options.model,
        active_settings,
    )

    prepare_started = time.perf_counter()
    try:
        await active_translator.prepare()
    except TranslationError as exc:
        return {
            "schema_version": 1,
            "status": "provider-unavailable",
            "generated_at": datetime.now(UTC).isoformat(),
            "provider": options.provider,
            "model": getattr(active_translator, "model", options.model),
            "prepare_ms": round((time.perf_counter() - prepare_started) * 1000.0, 1),
            "error": str(exc),
            "fixtures": [],
        }
    prepare_ms = round((time.perf_counter() - prepare_started) * 1000.0, 1)

    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    cer_values: list[float | None] = []
    wer_values: list[float | None] = []
    exact_matches = 0
    term_expected = 0
    term_matched = 0
    successful_runs = 0
    total_runs = len(fixtures) * options.repeats

    try:
        for fixture in fixtures:
            runs: list[dict[str, Any]] = []
            for repeat_index in range(options.repeats):
                started = time.perf_counter()
                try:
                    hypothesis = await active_translator.translate(
                        fixture.source_text,
                        source_language=fixture.source_language,
                        target_language=fixture.target_language,
                        context=fixture.context,
                    )
                    latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
                    quality = quality_metrics(fixture.reference_text, hypothesis)
                    terminology = terminology_metrics(fixture.expected_terms, hypothesis)
                    exact_match = normalize_text(hypothesis) == normalize_text(
                        fixture.reference_text
                    )
                    latencies.append(latency_ms)
                    cer_values.append(quality["cer"])
                    wer_values.append(quality["wer"])
                    exact_matches += int(exact_match)
                    term_expected += terminology["expected"]
                    term_matched += terminology["matched"]
                    successful_runs += 1
                    runs.append(
                        {
                            "repeat": repeat_index + 1,
                            "status": "ok",
                            "latency_ms": latency_ms,
                            "hypothesis": hypothesis,
                            "exact_match": exact_match,
                            "cer": quality["cer"],
                            "wer": quality["wer"],
                            "terminology": terminology,
                        }
                    )
                except TranslationError as exc:
                    runs.append(
                        {
                            "repeat": repeat_index + 1,
                            "status": "error",
                            "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                            "error": str(exc),
                        }
                    )
            results.append(
                {
                    "id": fixture.fixture_id,
                    "source_language": fixture.source_language,
                    "target_language": fixture.target_language,
                    "source_text": fixture.source_text,
                    "reference_text": fixture.reference_text,
                    "expected_terms": list(fixture.expected_terms),
                    "runs": runs,
                }
            )
    finally:
        await active_translator.close()

    success_rate = successful_runs / total_runs if total_runs else 0.0
    report = {
        "schema_version": 1,
        "status": "ok" if successful_runs == total_runs else "partial-failure",
        "generated_at": datetime.now(UTC).isoformat(),
        "provider": options.provider,
        "model": getattr(active_translator, "model", options.model),
        "config": {
            "fixtures_path": str(options.fixtures_path),
            "fixture_count": len(fixtures),
            "repeats": options.repeats,
            "total_runs": total_runs,
        },
        "summary": {
            "prepare_ms": prepare_ms,
            "successful_runs": successful_runs,
            "failed_runs": total_runs - successful_runs,
            "success_rate": round(success_rate, 4),
            "latency_mean_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "latency_p50_ms": percentile(latencies, 0.50),
            "latency_p95_ms": percentile(latencies, 0.95),
            "latency_max_ms": round(max(latencies), 1) if latencies else None,
            "cer_mean": _mean_optional(cer_values),
            "wer_mean": _mean_optional(wer_values),
            "exact_match_rate": (
                round(exact_matches / successful_runs, 4) if successful_runs else None
            ),
            "terminology_accuracy": (
                round(term_matched / term_expected, 4) if term_expected else None
            ),
            "terminology_matched": term_matched,
            "terminology_expected": term_expected,
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


def parse_args(argv: list[str] | None = None) -> TranslationBenchmarkOptions:
    parser = argparse.ArgumentParser(
        description="Benchmark LangTextFlow translation providers with JSONL fixtures"
    )
    parser.add_argument("fixtures", type=Path, help="JSONL translation fixture file")
    parser.add_argument(
        "--provider",
        choices=["ollama", "openai-compatible"],
        required=True,
    )
    parser.add_argument("--model", default=None, help="provider model id/name")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    return TranslationBenchmarkOptions(
        fixtures_path=args.fixtures,
        provider=args.provider,
        model=args.model,
        repeats=args.repeats,
        output_path=args.output,
    )


async def _async_main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    report = await run_translation_benchmark(options)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
