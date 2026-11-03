from __future__ import annotations

import pytest

from langtextflow.models import CaptionStage, TranscriptEvent
from langtextflow.runtime_benchmark import (
    RuntimeMetricsCollector,
    duplicate_metrics,
    failover_caption_gap_ms,
    latency_summary,
    max_timeline_gap_ms,
    parse_args,
)
from langtextflow.telemetry import RealtimeMetrics


def event(segment_id: str, start_ms: int, end_ms: int, text: str) -> TranscriptEvent:
    return TranscriptEvent(
        segment_id=segment_id,
        version=4,
        stage=CaptionStage.COMMITTED,
        source_language="ko",
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        committed=True,
    )


def test_runtime_benchmark_timeline_and_failover_gap() -> None:
    events = [
        event("a", 0, 1000, "첫 문장"),
        event("b", 1200, 2000, "둘째 문장"),
        event("c", 2400, 3000, "셋째 문장"),
    ]

    assert max_timeline_gap_ms(events) == 400
    assert failover_caption_gap_ms(events, 1500.0) == 0
    assert failover_caption_gap_ms(events, 2200.0) == 400
    assert failover_caption_gap_ms(events, None) is None


def test_runtime_benchmark_duplicate_metrics() -> None:
    events = [
        event("a", 0, 1000, "요한복음 3장"),
        event("b", 1000, 2000, " 요한복음   3장 "),
        event("c", 2000, 3000, "로마서 8장"),
    ]

    metrics = duplicate_metrics(events)

    assert metrics["exact_duplicate_count"] == 1
    assert metrics["exact_duplicate_rate"] == pytest.approx(1 / 3, abs=0.0001)


def test_runtime_metrics_collector_tracks_peaks_failover_and_latencies() -> None:
    collector = RuntimeMetricsCollector()
    first = RealtimeMetrics(
        audio_duration_ms=1200,
        asr_failover_count=0,
        postprocess_queue_depth=2,
        persistence_queue_depth=1,
        last_asr_lag_ms=300,
        last_correction_latency_ms=80,
    )
    second = RealtimeMetrics(
        audio_duration_ms=1500,
        asr_failover_count=1,
        postprocess_queue_depth=4,
        persistence_queue_depth=3,
        last_asr_lag_ms=420,
        last_correction_latency_ms=80,
        last_translation_latency_ms=600,
        last_commit_latency_ms=710,
    )

    collector.sample(first)
    collector.sample(second)

    assert collector.postprocess_queue_peak == 4
    assert collector.persistence_queue_peak == 3
    assert collector.failover_audio_ms == 1500
    assert collector.asr_latency_ms == [300.0, 420.0]
    assert collector.correction_latency_ms == [80.0]
    assert collector.translation_latency_ms == [600.0]
    assert collector.commit_latency_ms == [710.0]


def test_latency_summary_reports_percentiles() -> None:
    summary = latency_summary([100.0, 200.0, 300.0, 400.0])

    assert summary["count"] == 4
    assert summary["mean_ms"] == 250.0
    assert summary["p50_ms"] == 200.0
    assert summary["p95_ms"] == 400.0
    assert summary["max_ms"] == 400.0


async def test_runtime_benchmark_exposes_memory_scope_and_is_not_field_qualification(
    tmp_path, monkeypatch,
):
    import wave

    from test_faster_whisper import FakeFasterWhisperEngine

    from langtextflow.models import ProductPreset
    from langtextflow.runtime_benchmark import (
        InstrumentedRuntime,
        RuntimeBenchmarkOptions,
        run_runtime_benchmark,
    )

    monkeypatch.setattr(InstrumentedRuntime, "_build_engine", lambda runtime, _: (
        FakeFasterWhisperEngine(runtime.pipeline.ingest, chunk_seconds=.1)
    ))

    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 1600)
    result = await run_runtime_benchmark(RuntimeBenchmarkOptions(
        audio_path=path, engine="faster-whisper", source_language="ko", target_languages=("en",),
        translation_provider="none", translation_model=None, preset=ProductPreset.GENERAL,
        pace="max", chunk_ms=100, duration_minutes=None, settle_seconds=0,
        drain_timeout_seconds=1, reference_path=None, output_path=None,
        title="Synthetic memory wiring test", hotwords=(),
    ))
    assert result["field_ready"] is False
    assert result["environment"]["platform"]
    assert result["memory"]["scope"] == "parent_process"
    assert result["memory"]["process_tree"]["scope"] == "parent_and_recursive_descendants"
    assert result["memory"]["sample_count"] >= 3  # start, provider prepared, before shutdown


@pytest.mark.parametrize("provider", ["none", "demo", "ollama", "m2m100-ct2"])
def test_runtime_cli_accepts_two_target_soak_and_preserves_model_option(tmp_path, provider):
    audio = tmp_path / "fixture.wav"
    audio.touch()
    options = parse_args([
        str(audio), "--translation-provider", provider,
        "--target-language", "en", "--target-language", "ja",
        "--duration-minutes", "30", "--translation-model", "fixture-llm",
    ])
    assert options.translation_provider == provider
    assert options.target_languages == ("en", "ja")
    assert options.duration_minutes == 30
    assert options.translation_model == "fixture-llm"


@pytest.mark.parametrize("outcome", ["ready", "missing-cache", "target-failure"])
def test_runtime_cli_uses_product_m2m_settings_and_fails_source_only_runs(
    tmp_path, monkeypatch, outcome,
):
    import json
    import wave

    from test_faster_whisper import FakeFasterWhisperEngine

    from langtextflow import runtime_benchmark
    from langtextflow.config import Settings
    from langtextflow.translation import M2M100CT2Translator, TranslationError

    monkeypatch.setenv("LANGTEXTFLOW_RESOURCE_PROFILE", "standard")
    monkeypatch.setenv("LANGTEXTFLOW_LOCAL_M2M100_CT2_DIR", str(tmp_path / "ct2"))
    monkeypatch.setenv("LANGTEXTFLOW_LOCAL_M2M100_TOKENIZER_DIR", str(tmp_path / "tokenizer"))
    monkeypatch.setenv("LANGTEXTFLOW_LOCAL_M2M100_CPU_THREADS", "3")
    monkeypatch.setattr(runtime_benchmark, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(runtime_benchmark.InstrumentedRuntime, "_build_engine", lambda runtime, _: (
        FakeFasterWhisperEngine(runtime.pipeline.ingest, chunk_seconds=.1)
    ))
    prepared, translated, closed = [], [], []
    original_prepare = M2M100CT2Translator.prepare

    async def prepare(provider):
        prepared.append((provider.model_dir, provider.tokenizer_dir, provider.cpu_threads))
        if outcome == "missing-cache":
            await original_prepare(provider)  # Real missing-artifact guard, before native load.

    async def translate(provider, text, *, source_language, target_language, context):
        translated.append((source_language, target_language))
        if outcome == "target-failure" and target_language == "en":
            raise TranslationError("synthetic target failure")
        return f"{target_language}: synthetic translation"

    async def close(provider):
        closed.append(provider)

    monkeypatch.setattr(M2M100CT2Translator, "prepare", prepare)
    monkeypatch.setattr(M2M100CT2Translator, "translate", translate)
    monkeypatch.setattr(M2M100CT2Translator, "close", close)
    audio = tmp_path / "synthetic.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 1600)
    output = tmp_path / "report.json"
    code = runtime_benchmark.main([
        str(audio), "--engine", "faster-whisper", "--source-language", "ko",
        "--translation-provider", "m2m100-ct2",
        "--target-language", "en", "--target-language", "ja",
        "--pace", "max", "--settle-seconds", "0", "--drain-timeout-seconds", "1",
        "--output", str(output),
    ])
    report = json.loads(output.read_text())
    assert prepared == [(tmp_path / "ct2", tmp_path / "tokenizer", 3)]
    assert len(closed) == 1
    assert report["config"]["target_languages"] == ["en", "ja"]
    assert report["quality"]["committed_segment_count"] == 1
    assert report["transcript"] == "chunk-1"  # Source preserved even when MT cannot run.
    assert report["translation"]["provider"] == "m2m100-ct2"
    assert code == (0 if outcome == "ready" else 1)
    assert report["status"] == ("ok" if outcome == "ready" else "failed")
    assert report["translation"]["failed_target_count"] == {
        "ready": 0, "missing-cache": 2, "target-failure": 1,
    }[outcome]
    if outcome == "missing-cache":
        assert not report["translation"]["available"]
        assert "cache is missing" in report["translation"]["error"]
        assert translated == []
    else:
        assert report["translation"]["available"]
        assert sorted(translated) == [("ko", "en"), ("ko", "ja")]


@pytest.mark.parametrize("status,exit_code", [("ok", 0), ("failed", 1), ("drain-timeout", 1)])
def test_runtime_cli_exit_matches_saved_report(tmp_path, monkeypatch, status, exit_code):
    import json

    from langtextflow import runtime_benchmark

    async def run(options):
        return {"status": status}

    monkeypatch.setattr(runtime_benchmark, "run_runtime_benchmark", run)
    audio = tmp_path / "fixture.wav"
    audio.touch()
    output = tmp_path / "report.json"
    assert runtime_benchmark.main([str(audio), "--output", str(output)]) == exit_code
    assert json.loads(output.read_text())["status"] == status
