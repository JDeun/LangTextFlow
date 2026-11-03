"""E2E runtime pilot must never count untranslated source as working English."""

from __future__ import annotations

import pytest

from scripts.evaluate_live_fleurs_translation import summarize_events


def event(id: str, stage: str, received: float, translated: str = "") -> dict:
    return {
        "segment_id": id,
        "stage": stage,
        "version": 1,
        "text": "믿음은 중요합니다.",
        "start_ms": 0,
        "end_ms": 2000,
        "received_ms": received,
        "translations": {"en": translated} if translated else {},
    }


def test_untranslated_caption_cannot_fake_successful_interpretation() -> None:
    result = summarize_events(
        [event("1", "stable", 2500), event("1", "committed", 2600)],
        2,
        2.6,
    )
    assert result["committed_count"] == 1
    assert result["translated_committed_count"] == 0
    assert result["translation_coverage"] == 0
    assert result["translated_hypothesis"] == ""
    assert result["field_ready"] is False


def test_actual_translated_committed_event_has_end_to_end_lag() -> None:
    rows = [
        event("1", "stable", 2500),
        event("1", "translated", 4600, "Faith is important."),
        event("1", "committed", 4700, "Faith is important."),
    ]
    result = summarize_events(rows, 2, 4.7)
    assert result["translation_coverage"] == 1.0
    assert result["p95_lag_ms"]["stable"] == 500
    assert result["p95_lag_ms"]["translated"] == 2600
    assert result["processing_rtf"] == 2.35
    assert result["field_ready"] is False


def test_missing_or_invalid_audio_duration_is_not_a_pass() -> None:
    with pytest.raises(ValueError, match="positive"):
        summarize_events([], 0, 0.5)


def test_out_of_bounds_source_time_is_not_falsely_low_latency() -> None:
    rows = [
        event("invalid", "stable", 2200),
        event("invalid", "translated", 2500, "Faith is important."),
    ]
    for row in rows:
        row["end_ms"] = 15000
    summary = summarize_events(rows, 2, 2.5)
    assert summary["invalid_timestamp_count"] == 1
    assert summary["invalid_timestamp_segment_ids"] == ["invalid"]
    assert summary["p95_lag_ms"]["translated"] is None
    assert summary["field_ready"] is False


def test_english_source_korean_target_and_raw_evidence_are_independent() -> None:
    rows = [event("a", "stable", 2500), event("a", "committed", 3000)]
    for row in rows:
        row.update(
            source_language="en",
            text="Corrected source",
            original_text="Raw source",
            translations={"ko": "한국어 번역"},
            translation_states={"ko": "available"},
            source_revision=2,
        )
    rows.append({**rows[-1], "version": 0, "source_revision": 1, "text": "obsolete"})
    result = summarize_events(rows, 2, 3, source_language="en", target_language="ko")
    assert result["translated_hypothesis"] == "한국어 번역"
    assert result["asr_hypothesis"] == "Raw source"
    assert result["caption_source_hypothesis"] == "Corrected source"
    assert result["committed_count"] == 1
    assert result["target_states"] == {"available": 1}


def test_unavailable_target_does_not_hide_other_target_or_count_source() -> None:
    row = event("a", "committed", 2500)
    row.update(
        translations={"ja": "日本語訳"}, translation_states={"en": "failed", "ja": "available"}
    )
    failed_target_event = {**row, "stage": "translated"}
    english = summarize_events([failed_target_event, row], 2, 2.5)
    japanese = summarize_events([failed_target_event, row], 2, 2.5, target_language="ja")
    assert english["translation_coverage"] == 0
    assert english["target_states"] == {"failed": 1}
    assert english["first_translation_ms"] is None
    assert english["p95_lag_ms"]["translated"] is None
    assert japanese["p95_lag_ms"]["translated"] == 500
    assert japanese["translation_coverage"] == 1


@pytest.fixture
def fake_native_inference(monkeypatch):
    """Real audio queue/pipeline/provider/hub; only native model weights are fake."""
    from langtextflow.asr.faster_whisper import DecodedSegment, FasterWhisperStreamingAsrEngine
    from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator

    monkeypatch.setattr(FasterWhisperStreamingAsrEngine, "_load_model_sync", lambda self: object())
    monkeypatch.setattr(
        FasterWhisperStreamingAsrEngine,
        "_transcribe_chunk_sync",
        lambda self, pcm, request: [
            DecodedSegment(
                0,
                len(pcm) / 64000,
                "Raw source" if request.source_language == "en" else "원문 증거",
            )
        ],
    )

    async def prepare(self):
        self._engine = self._tokenizer = object()

    monkeypatch.setattr(M2M100CT2Translator, "prepare", prepare)
    monkeypatch.setattr(
        M2M100CT2Translator,
        "_translate_sync",
        lambda self, text, source, target, tokenizer, engine: (
            "한국어 번역" if target == "ko" else "English translation"
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ko", "en"])
async def test_live_benchmark_uses_actual_queue_provider_and_serialized_fanout(
    tmp_path,
    fake_native_inference,
    source,
) -> None:
    import asyncio

    from scripts.evaluate_live_fleurs_translation import run_live

    report = await run_live(
        b"\x01\x00" * 1600,
        "Raw source" if source == "en" else "원문 증거",
        [],
        tmp_path,
        model="tiny",
        chunk_seconds=4,
        timeout_seconds=3,
        source_language=source,
        translation_provider="m2m100-ct2",
        measurement_mode="mock-wiring",
    )
    target = "ko" if source == "en" else "en"
    assert report["measurement_mode"] == "mock-wiring"
    assert report["translation_model"] == "facebook/m2m100_418M"
    assert report["audience_observation"]["closed_on_cleanup"] is True
    assert report["queues_drained"] is True
    assert report["asr_quality"]["cer"] == 0
    assert report["metrics"]["target_states"] == {"available": 1}
    assert report["translation_quality"]["chrf"] is None
    assert report["translation_quality"]["status"] == "unavailable"
    assert report["audience_observation"]["network_or_browser_paint"] is False
    assert report["audience_observation"]["snapshots"] == 1
    assert report["stages"][-1]["selected_language"] == target
    assert report["stages"][-1]["selected_caption"] != report["stages"][-1]["text"]
    assert report["stages"][-1]["original_text"]
    assert report["stages"][-1]["source_revision"] >= 1
    assert {row["session_id"] for row in report["stages"]} == {
        report["audience_observation"]["session_id"]
    }
    assert not any(t.get_name() == "live-benchmark-resources" for t in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_failed_start_writes_bounded_report_and_cleans_sampler(tmp_path, monkeypatch) -> None:
    import asyncio
    import json

    from langtextflow.runtime import CaptionRuntime
    from scripts.evaluate_live_fleurs_translation import run_live

    async def fail(self, request):
        raise RuntimeError("startup failed " + "x" * 5000)

    monkeypatch.setattr(CaptionRuntime, "start", fail)
    with pytest.raises(RuntimeError, match="startup failed"):
        await run_live(
            b"\0\0" * 1600, "source", [], tmp_path, model="tiny", chunk_seconds=4, timeout_seconds=1
        )
    report = json.loads((tmp_path / "live-interpretation-report.json").read_text())
    assert report["status"] == "runtime-error"
    assert len(report["error"]) < 1100
    assert report["startup_seconds"] is None
    assert not any(t.get_name() == "live-benchmark-resources" for t in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_observer_disconnect_is_failed_measurement_not_fake_success(
    tmp_path,
    monkeypatch,
    fake_native_inference,
) -> None:
    import json

    from scripts.evaluate_live_fleurs_translation import AudienceCollector, run_live

    original = AudienceCollector.send_json

    async def disconnected(self, payload):
        if payload.get("type") == "transcript":
            raise RuntimeError("observer disconnected")
        await original(self, payload)

    monkeypatch.setattr(AudienceCollector, "send_json", disconnected)
    with pytest.raises(RuntimeError, match="observer disconnected"):
        await run_live(
            b"\1\0" * 1600,
            "source",
            [],
            tmp_path,
            model="tiny",
            chunk_seconds=4,
            timeout_seconds=2,
            translation_provider="m2m100-ct2",
            measurement_mode="mock-wiring",
        )
    report = json.loads((tmp_path / "live-interpretation-report.json").read_text())
    assert report["telemetry"]["websocket_send_failures"] == 1
    assert report["metrics"]["translation_coverage"] == 0


def test_report_serialization_failure_leaves_small_failure_json(tmp_path) -> None:
    import json

    from scripts.evaluate_live_fleurs_translation import write_report

    with pytest.raises(TypeError):
        write_report(tmp_path, {"unsupported": object()})
    failure = json.loads((tmp_path / "failure-report.json").read_text())
    assert failure["status"] == "report-error"
    assert len(failure["error"]) <= 1000


def test_failed_new_invocation_never_reuses_previous_success(tmp_path, monkeypatch) -> None:
    import json
    import sys

    import scripts.evaluate_live_fleurs_translation as benchmark

    report = tmp_path / "live-interpretation-report.json"
    report.write_text(json.dumps({"status": "measured", "old_run": True}))
    monkeypatch.setattr(sys, "argv", ["benchmark", "--output", str(tmp_path)])

    def unavailable(*args):
        raise RuntimeError("pinned dataset unavailable")

    monkeypatch.setattr(benchmark, "load_pinned_speech", unavailable)
    with pytest.raises(RuntimeError, match="pinned dataset unavailable"):
        benchmark.main()
    result = json.loads(report.read_text())
    assert result["status"] == "runtime-error"
    assert "old_run" not in result


@pytest.mark.asyncio
async def test_audience_observer_rejects_wrong_scope_and_bounds_events(monkeypatch) -> None:
    import scripts.evaluate_live_fleurs_translation as benchmark

    observer = benchmark.AudienceCollector("session-a", "en", "ko")
    with pytest.raises(ValueError, match="source scope"):
        await observer.send_json({"type": "transcript", "source_language": "ko"})
    assert observer.scope_errors == 1
    assert observer.events == []
    monkeypatch.setattr(benchmark, "MAX_EVENTS", 1)
    payload = {
        "type": "transcript",
        "source_language": "en",
        "translations": {},
        "translation_states": {"ko": "failed"},
    }
    await observer.send_json(payload)
    with pytest.raises(RuntimeError, match="budget exceeded"):
        await observer.send_json(payload)
    assert len(observer.events) == 1
    assert observer.events[0]["selected_caption"] is None
    assert observer.events[0]["selected_target_state"] == "failed"
    assert observer.overflow == 1


def test_model_hashes_record_content_without_copying_models(tmp_path) -> None:
    import hashlib

    from scripts.evaluate_live_fleurs_translation import model_hashes

    (tmp_path / "model.bin").write_bytes(b"synthetic weights")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "unrelated.txt").write_text("not model provenance")
    hashes = model_hashes(tmp_path)
    assert hashes == {
        "model.bin": hashlib.sha256(b"synthetic weights").hexdigest(),
        "config.json": hashlib.sha256(b"{}").hexdigest(),
    }
