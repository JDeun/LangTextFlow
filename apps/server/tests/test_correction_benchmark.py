from __future__ import annotations

import json
from pathlib import Path

import pytest

from langtextflow.correction_benchmark import (
    CorrectionBenchmarkOptions,
    classification_metrics,
    critical_token_metrics,
    load_fixtures,
    numeric_token_metrics,
    run_correction_benchmark,
)
from langtextflow.llm_correction import ConstrainedCorrector, CorrectionError
from langtextflow.models import SessionContext


class FakeCorrector(ConstrainedCorrector):
    provider = "fake"
    model = "fake-corrector"

    def __init__(
        self,
        outputs: dict[str, str] | None = None,
        *,
        error: str | None = None,
        prepare_error: str | None = None,
    ) -> None:
        self.outputs = outputs or {}
        self.error = error
        self.prepare_error = prepare_error
        self.closed = False

    async def prepare(self) -> None:
        if self.prepare_error:
            raise CorrectionError(self.prepare_error)

    async def correct(
        self,
        text: str,
        *,
        source_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, context
        if self.error:
            raise CorrectionError(self.error)
        return self.outputs.get(text, text)

    async def close(self) -> None:
        self.closed = True


def write_fixtures(tmp_path: Path) -> Path:
    path = tmp_path / "correction.jsonl"
    rows = [
        {
            "id": "alias",
            "source_language": "ko",
            "source_text": "요한 보금 3장 말씀입니다",
            "expected_text": "요한복음 3장 말씀입니다",
            "critical_tokens": ["요한복음", "3장"],
            "context": {"preset": "church"},
        },
        {
            "id": "already-correct",
            "source_language": "ko",
            "source_text": "복음은 은혜입니다",
            "expected_text": "복음은 은혜입니다",
            "critical_tokens": ["복음", "은혜"],
            "context": {"preset": "church"},
        },
        {
            "id": "llm-needed",
            "source_language": "ko",
            "source_text": "요한 보끔 3장 16절 말씀입니다",
            "expected_text": "요한복음 3장 16절 말씀입니다",
            "critical_tokens": ["요한복음", "3장", "16절"],
            "context": {
                "preset": "church",
                "reference_text": "본문은 요한복음 3장 16절입니다.",
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def test_fixture_loader_and_atomic_metrics(tmp_path: Path) -> None:
    fixtures = load_fixtures(write_fixtures(tmp_path))

    assert len(fixtures) == 3
    assert fixtures[0].critical_tokens == ("요한복음", "3장")
    assert fixtures[0].context.preset.value == "church"
    assert critical_token_metrics(("요한복음", "3장"), "요한복음 3장") == {
        "expected": 2,
        "matched": 2,
        "accuracy": 1.0,
        "missing": [],
    }
    assert numeric_token_metrics("요한복음 3장 16절", "요한복음 3장 16절")["match"] is True
    assert classification_metrics(3, 1, 1, 5) == {
        "tp": 3,
        "fp": 1,
        "fn": 1,
        "tn": 5,
        "precision": 0.75,
        "recall": 0.75,
        "f1": 0.75,
    }


def test_fixture_loader_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.jsonl"
    row = {
        "id": "same",
        "source_language": "ko",
        "source_text": "성녕",
        "expected_text": "성령",
    }
    path.write_text(
        json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate fixture id"):
        load_fixtures(path)


@pytest.mark.asyncio
async def test_deterministic_benchmark_reports_missed_correction_without_harm(
    tmp_path: Path,
) -> None:
    options = CorrectionBenchmarkOptions(
        fixtures_path=write_fixtures(tmp_path),
        provider="none",
        model=None,
        repeats=1,
        output_path=None,
    )

    report = await run_correction_benchmark(options)

    summary = report["summary"]
    assert report["provider_available"] is True
    assert summary["exact_correct_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert summary["harmful_change_rate"] == 0.0
    assert summary["missed_correction_rate"] == 0.5
    assert summary["wrong_change_rate"] == 0.0
    assert summary["change_detection"]["tp"] == 1
    assert summary["change_detection"]["fn"] == 1
    assert summary["change_detection"]["tn"] == 1
    assert summary["critical_token_accuracy"] == pytest.approx(6 / 7, abs=0.0001)
    assert summary["numeric_token_accuracy"] == 1.0


@pytest.mark.asyncio
async def test_llm_benchmark_measures_successful_correction_and_writes_report(
    tmp_path: Path,
) -> None:
    fixtures_path = write_fixtures(tmp_path)
    output_path = tmp_path / "report.json"
    corrector = FakeCorrector(
        outputs={
            "요한 보끔 3장 16절 말씀입니다": "요한복음 3장 16절 말씀입니다",
        }
    )
    options = CorrectionBenchmarkOptions(
        fixtures_path=fixtures_path,
        provider="ollama",
        model="fake-corrector",
        repeats=2,
        output_path=output_path,
    )

    report = await run_correction_benchmark(options, llm_corrector=corrector)

    summary = report["summary"]
    assert corrector.closed is True
    assert summary["exact_correct_rate"] == 1.0
    assert summary["harmful_change_rate"] == 0.0
    assert summary["missed_correction_rate"] == 0.0
    assert summary["wrong_change_rate"] == 0.0
    assert summary["critical_token_accuracy"] == 1.0
    assert summary["numeric_token_accuracy"] == 1.0
    assert summary["llm_attempted_count"] == 6
    assert summary["llm_applied_count"] == 6
    assert summary["fallback_count"] == 0
    assert summary["latency_p95_ms"] is not None
    assert report["config"]["fixture_sha256"]
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["environment"]["python"]
    assert persisted["summary"]["exact_correct_rate"] == 1.0


@pytest.mark.asyncio
async def test_benchmark_detects_harmful_and_wrong_changes(tmp_path: Path) -> None:
    corrector = FakeCorrector(
        outputs={
            "요한복음 3장 말씀입니다": "요한복음 4장 말씀입니다",
            "복음은 은혜입니다": "복음은 행위입니다",
            "요한 보끔 3장 16절 말씀입니다": "요한복음 4장 16절 말씀입니다",
        }
    )
    options = CorrectionBenchmarkOptions(
        fixtures_path=write_fixtures(tmp_path),
        provider="ollama",
        model="fake-corrector",
        repeats=1,
        output_path=None,
    )

    report = await run_correction_benchmark(options, llm_corrector=corrector)
    summary = report["summary"]

    assert summary["harmful_change_count"] == 1
    assert summary["harmful_change_rate"] == 1.0
    assert summary["wrong_change_count"] == 2
    assert summary["wrong_change_rate"] == 1.0
    assert summary["numeric_token_accuracy"] < 1.0
    assert summary["change_detection"]["fp"] == 1


@pytest.mark.asyncio
async def test_llm_failure_falls_back_and_is_counted(tmp_path: Path) -> None:
    corrector = FakeCorrector(error="unsafe rewrite")
    options = CorrectionBenchmarkOptions(
        fixtures_path=write_fixtures(tmp_path),
        provider="ollama",
        model="fake-corrector",
        repeats=1,
        output_path=None,
    )

    report = await run_correction_benchmark(options, llm_corrector=corrector)

    assert report["provider_available"] is True
    assert report["summary"]["fallback_count"] == 3
    assert report["summary"]["llm_attempted_count"] == 3
    assert report["summary"]["llm_applied_count"] == 0
    assert all(
        run["method"] == "fallback"
        for fixture in report["fixtures"]
        for run in fixture["runs"]
    )


@pytest.mark.asyncio
async def test_unavailable_llm_uses_deterministic_without_attempt(tmp_path: Path) -> None:
    corrector = FakeCorrector(prepare_error="model unavailable")
    options = CorrectionBenchmarkOptions(
        fixtures_path=write_fixtures(tmp_path),
        provider="ollama",
        model="fake-corrector",
        repeats=1,
        output_path=None,
    )

    report = await run_correction_benchmark(options, llm_corrector=corrector)

    assert corrector.closed is True
    assert report["provider_available"] is False
    assert report["provider_error"] == "model unavailable"
    assert report["summary"]["fallback_count"] == 3
    assert report["summary"]["llm_attempted_count"] == 0
