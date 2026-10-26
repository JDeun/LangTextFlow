"""End-to-end wiring regression: corpus -> local adapter -> report -> assessment."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_six_direction_sermon_evaluation_wiring() -> None:
    evaluator = _load("evaluate_sermon_mt")
    assessor = _load("assess_sermon_mt")
    with (
        patch.object(evaluator.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(evaluator.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(evaluator.M2M100CT2Translator, "close", new_callable=AsyncMock),
    ):
        mt.return_value = "simulated model translation"
        report = await evaluator.evaluate("local-model", "local-tokenizer", 2)
    assert report["failure_count"] == 0
    assert mt.await_count == 9
    assert {
        (row["source_language"], row["target_language"]) for row in report["records"]
    } == {
        (source, target) for source in ("ko", "en", "ja")
        for target in ("ko", "en", "ja") if source != target
    }
    incomplete = assessor.assess(report)
    assert incomplete["overall_status"] == "not_verified"
    assert all(
        item["quality_status"] == "needs_review"
        for item in incomplete["directions"].values()
    )
    reviews = {
        row["id"]: {"terminology_correct": True, "meaning_preserved": True}
        for row in report["records"]
    }
    accepted = assessor.assess(report, reviews, max_p95_ms=10000)
    assert accepted["overall_status"] == "pass"
    assert len(accepted["directions"]) == 6


@pytest.mark.asyncio
async def test_sermon_evaluation_failure_blocks_direction() -> None:
    evaluator = _load("evaluate_sermon_mt")
    assessor = _load("assess_sermon_mt")
    with (
        patch.object(evaluator.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(evaluator.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(evaluator.M2M100CT2Translator, "close", new_callable=AsyncMock),
    ):
        mt.side_effect = [RuntimeError("native inference failed")] + ["translated"] * 8
        report = await evaluator.evaluate("local-model", "local-tokenizer", 2)
    reviews = {
        row["id"]: {"terminology_correct": True, "meaning_preserved": True}
        for row in report["records"]
    }
    result = assessor.assess(report, reviews, max_p95_ms=10000)
    assert result["overall_status"] == "not_verified"
    assert result["directions"]["ko->en"]["runtime_status"] == "fail"
