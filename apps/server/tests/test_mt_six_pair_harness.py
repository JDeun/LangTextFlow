"""Six-pair smoke harness exercises all supported directed language pairs."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_m2m100_six_pairs.py"


@pytest.mark.asyncio
async def test_real_model_harness_covers_six_directed_pairs() -> None:
    spec = importlib.util.spec_from_file_location("verify_m2m100_six_pairs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        patch.object(module.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(module.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(module.M2M100CT2Translator, "close", new_callable=AsyncMock),
    ):
        mt.return_value = "translated caption"
        report = await module.verify("model", "tokenizer", 2)
    assert len(report["results"]) == 6
    assert {(row["source"], row["target"]) for row in report["results"]} == {
        ("ko", "en"), ("ko", "ja"), ("en", "ko"),
        ("en", "ja"), ("ja", "ko"), ("ja", "en"),
    }
    assert all(row["status"] == "passed" for row in report["results"])
