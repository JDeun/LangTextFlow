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
    assert all(row["successful_runs"] == 1 for row in report["results"])
    assert all(row["p50_ms"] is not None for row in report["results"])


@pytest.mark.asyncio
async def test_benchmark_repetitions_and_failed_run_are_reported() -> None:
    spec = importlib.util.spec_from_file_location("verify_m2m100_six_pairs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        patch.object(module.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(module.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(module.M2M100CT2Translator, "close", new_callable=AsyncMock),
    ):
        mt.side_effect = ["caption", RuntimeError("test failure")] * 6
        report = await module.verify("model", "tokenizer", 2, repetitions=2)
    assert len(report["results"]) == 6
    assert all(row["status"] == "failed" for row in report["results"])
    assert all(row["successful_runs"] == 1 for row in report["results"])
    assert all(row["errors"] == ["RuntimeError"] for row in report["results"])
    assert all(row["p95_ms"] is not None for row in report["results"])


def test_linux_rss_parser_handles_missing_and_valid_proc_status() -> None:
    spec = importlib.util.spec_from_file_location("verify_m2m100_six_pairs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(module.Path, "read_text", return_value=(
        "Name:\tpython\nVmHWM:\t8192 kB\nVmRSS:\t4096 kB\n"
    )):
        assert module._linux_memory_kib() == {
            "rss_kib": 4096, "peak_rss_kib": 8192,
        }
    with patch.object(module.Path, "read_text", side_effect=OSError):
        assert module._linux_memory_kib() is None


@pytest.mark.asyncio
async def test_memory_snapshot_occurs_before_provider_close() -> None:
    spec = importlib.util.spec_from_file_location("verify_m2m100_six_pairs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = []

    async def close() -> None:
        events.append("close")

    def memory() -> dict[str, int]:
        events.append("memory")
        return {"rss_kib": 1, "peak_rss_kib": 2}

    with (
        patch.object(module.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(module.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(module.M2M100CT2Translator, "close", side_effect=close),
        patch.object(module, "_linux_memory_kib", side_effect=memory),
    ):
        mt.return_value = "caption"
        report = await module.verify("model", "tokenizer", 2)
    assert events == ["memory", "memory", "memory", "close"]
    assert report["memory_after_inference"] == {"rss_kib": 1, "peak_rss_kib": 2}


@pytest.mark.asyncio
async def test_warmup_inference_excluded_from_measured_runs() -> None:
    spec = importlib.util.spec_from_file_location("verify_m2m100_six_pairs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (
        patch.object(module.M2M100CT2Translator, "prepare", new_callable=AsyncMock),
        patch.object(module.M2M100CT2Translator, "translate", new_callable=AsyncMock) as mt,
        patch.object(module.M2M100CT2Translator, "close", new_callable=AsyncMock),
    ):
        mt.return_value = "translated"
        report = await module.verify("model", "tokenizer", 2, repetitions=2, warmup_runs=1)
    assert mt.await_count == 18
    assert report["warmup_runs"] == 1
    assert all(row["successful_runs"] == 2 for row in report["results"])
    assert all(row["runs"] == 2 and row["warmup_runs"] == 1 for row in report["results"])
