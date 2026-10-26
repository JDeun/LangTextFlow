"""The opt-in sermon evaluation runner preserves results for every input."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "evaluate_sermon_mt.py"


@pytest.mark.asyncio
async def test_sermon_runner_retains_each_result_and_failure() -> None:
    spec = importlib.util.spec_from_file_location("evaluate_sermon_mt", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        patch.object(module.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(module.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(module.M2M100CT2Translator, "close", new_callable=AsyncMock) as close,
    ):
        mt.side_effect = [RuntimeError("offline model failure")] + ["translation"] * 8
        report = await module.evaluate("model", "tokenizer", 2)
    assert report["validation"]["samples"] == 9
    assert len(report["records"]) == 9
    assert report["failure_count"] == 1
    assert report["success_count"] == 8
    assert report["records"][0]["error"].startswith("RuntimeError:")
    assert report["records"][0]["output"] is None
    assert all(row["latency_ms"] >= 0 for row in report["records"])
    assert report["quality_review_required"] is True
    close.assert_awaited_once()
