from __future__ import annotations

import json
from pathlib import Path

import pytest

from langtextflow.models import SessionContext
from langtextflow.translation.base import TranslationError, Translator
from langtextflow.translation_benchmark import (
    TranslationBenchmarkOptions,
    load_fixtures,
    run_translation_benchmark,
    terminology_metrics,
)


class FakeTranslator(Translator):
    provider = "fake"
    model = "fake-model"

    def __init__(self, outputs: dict[str, str], *, fail_on: set[str] | None = None) -> None:
        self.outputs = outputs
        self.fail_on = fail_on or set()
        self.prepared = False
        self.closed = False

    async def prepare(self) -> None:
        self.prepared = True

    async def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, target_language, context
        if text in self.fail_on:
            raise TranslationError("synthetic failure")
        return self.outputs[text]

    async def close(self) -> None:
        self.closed = True


def write_fixtures(tmp_path: Path) -> Path:
    path = tmp_path / "translation.jsonl"
    rows = [
        {
            "id": "ko-en-1",
            "source_language": "ko",
            "target_language": "en",
            "source_text": "요한복음을 읽겠습니다",
            "reference_text": "We will read John.",
            "expected_terms": ["John"],
            "context": {
                "preset": "church",
                "glossary": [
                    {
                        "term": "요한복음",
                        "translations": {"en": "John"},
                    }
                ],
            },
        },
        {
            "id": "en-ko-1",
            "source_language": "en",
            "target_language": "ko",
            "source_text": "Welcome to the session.",
            "reference_text": "세션에 오신 것을 환영합니다.",
            "expected_terms": ["세션"],
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_load_fixtures_reads_context_and_terms(tmp_path: Path) -> None:
    path = write_fixtures(tmp_path)
    fixtures = load_fixtures(path)

    assert len(fixtures) == 2
    assert fixtures[0].fixture_id == "ko-en-1"
    assert fixtures[0].expected_terms == ("John",)
    assert fixtures[0].context.preset.value == "church"
    assert fixtures[0].context.glossary[0].translations["en"] == "John"


def test_load_fixtures_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    row = {
        "id": "duplicate",
        "source_language": "ko",
        "target_language": "en",
        "source_text": "안녕하세요",
        "reference_text": "Hello",
    }
    path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate fixture id"):
        load_fixtures(path)


def test_terminology_metrics_reports_missing_terms() -> None:
    metrics = terminology_metrics(("John", "grace"), "John chapter three")

    assert metrics == {
        "expected": 2,
        "matched": 1,
        "accuracy": 0.5,
        "missing": ["grace"],
    }


@pytest.mark.asyncio
async def test_translation_benchmark_aggregates_quality_latency_and_output(
    tmp_path: Path,
) -> None:
    fixtures_path = write_fixtures(tmp_path)
    output_path = tmp_path / "result.json"
    translator = FakeTranslator(
        {
            "요한복음을 읽겠습니다": "We will read John.",
            "Welcome to the session.": "세션에 오신 것을 환영합니다.",
        }
    )
    options = TranslationBenchmarkOptions(
        fixtures_path=fixtures_path,
        provider="ollama",
        model="fake-model",
        repeats=2,
        output_path=output_path,
    )

    report = await run_translation_benchmark(options, translator=translator)

    assert translator.prepared is True
    assert translator.closed is True
    assert report["status"] == "ok"
    assert report["summary"]["successful_runs"] == 4
    assert report["summary"]["failed_runs"] == 0
    assert report["summary"]["success_rate"] == 1.0
    assert report["summary"]["exact_match_rate"] == 1.0
    assert report["summary"]["cer_mean"] == 0.0
    assert report["summary"]["wer_mean"] == 0.0
    assert report["summary"]["terminology_accuracy"] == 1.0
    assert report["summary"]["latency_p50_ms"] is not None
    assert report["summary"]["latency_p95_ms"] is not None
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["summary"]["successful_runs"] == 4


@pytest.mark.asyncio
async def test_translation_benchmark_records_partial_failure(tmp_path: Path) -> None:
    fixtures_path = write_fixtures(tmp_path)
    translator = FakeTranslator(
        {
            "요한복음을 읽겠습니다": "We will read John.",
            "Welcome to the session.": "unused",
        },
        fail_on={"Welcome to the session."},
    )
    options = TranslationBenchmarkOptions(
        fixtures_path=fixtures_path,
        provider="openai-compatible",
        model="fake-model",
        repeats=1,
        output_path=None,
    )

    report = await run_translation_benchmark(options, translator=translator)

    assert report["status"] == "partial-failure"
    assert report["summary"]["successful_runs"] == 1
    assert report["summary"]["failed_runs"] == 1
    assert report["summary"]["success_rate"] == 0.5
    failed = report["fixtures"][1]["runs"][0]
    assert failed["status"] == "error"
    assert failed["error"] == "synthetic failure"
