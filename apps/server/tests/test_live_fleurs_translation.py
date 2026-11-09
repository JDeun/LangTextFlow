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
                {"en": "Raw source", "ko": "원문 증거", "ja": "原文の証拠"}[
                    request.source_language
                ],
            )
        ],
    )

    async def prepare(self):
        pass

    monkeypatch.setattr(M2M100CT2Translator, "prepare", prepare)
    async def infer(self, text, source, target):
        return {"ko": "한국어 번역", "en": "English translation", "ja": "日本語訳"}[target]

    monkeypatch.setattr(M2M100CT2Translator, "_translate_rpc", infer)


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
    assert report["asr_quality"]["cer"] is None
    assert report["asr_completed"] is False
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
            "원문 증거",
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
    assert report["asr_quality"]["cer"] == 0
    assert report["asr_quality_surface"] == "runtime_store_committed_original"
    assert report["audience_observation"]["server_committed_count"] == 1
    assert report["audience_observation"]["observed_committed_count"] == 0


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


@pytest.mark.asyncio
async def test_japanese_dual_target_runtime_persists_and_keeps_gold_out_of_context(
    tmp_path, fake_native_inference, monkeypatch,
) -> None:
    import sqlite3

    from langtextflow.runtime import CaptionRuntime
    from scripts.evaluate_live_fleurs_translation import run_live

    original = CaptionRuntime.start

    async def inspected(self, request):
        assert request.source_language == "ja"
        assert request.target_languages == ["ko", "en"]
        assert request.resource_profile == "low"
        assert "GOLD_SENTINEL" not in request.context.model_dump_json()
        return await original(self, request)

    monkeypatch.setattr(CaptionRuntime, "start", inspected)
    report = await run_live(
        b"\x01\x00" * 4000, "原文の証拠", [], tmp_path,
        model="base", chunk_seconds=4, timeout_seconds=3,
        source_language="ja", target_languages=["ko", "en"],
        translation_provider="m2m100-ct2", resource_profile="low",
        measurement_mode="mock-wiring",
        reference_provenance={
            "translation_references": {"ko": "GOLD_SENTINEL_KO", "en": "GOLD_SENTINEL_EN"}
        },
    )
    assert report["status"] == "measured"
    assert report["asr_quality"]["cer"] == 0
    assert set(report["per_target_metrics"]) == {"ko", "en"}
    for target in ("ko", "en"):
        assert report["per_target_metrics"][target]["translation_coverage"] == 1
        assert report["target_stages"][target][-1]["selected_language"] == target
        assert report["audience_observation"]["per_target"][target]["closed_on_cleanup"]
    assert report["runtime_budget"]["asr_cpu_threads"] == 2
    assert report["runtime_budget"]["effective_parallel_target_limit"] == 1
    assert report["runtime_budget"]["effective_postprocess_workers"] == 1
    assert report["reference_provenance"]["translation_references"]["ko"] == "GOLD_SENTINEL_KO"
    with sqlite3.connect(tmp_path / "live-benchmark.sqlite") as db:
        assert db.execute("SELECT ended_at FROM sessions").fetchone()[0] is not None


@pytest.mark.asyncio
async def test_failed_peer_target_remains_visible_and_fails_measurement(
    tmp_path, fake_native_inference, monkeypatch,
) -> None:
    import json

    from langtextflow.translation.base import TranslationRejected
    from langtextflow.translation.m2m100_ct2 import M2M100CT2Translator
    from scripts.evaluate_live_fleurs_translation import run_live

    async def rejected_peer(self, text, source, target):
        if target == "en":
            raise TranslationRejected("synthetic rejected output")
        return "한국어 번역"

    monkeypatch.setattr(M2M100CT2Translator, "_translate_rpc", rejected_peer)
    with pytest.raises(RuntimeError, match="live interpretation pipeline failed"):
        await run_live(
            b"\x01\x00" * 4000, "原文の証拠", [], tmp_path,
            model="base", chunk_seconds=4, timeout_seconds=3,
            source_language="ja", target_languages=["ko", "en"],
            translation_provider="m2m100-ct2", measurement_mode="mock-wiring",
        )
    report = json.loads((tmp_path / "live-interpretation-report.json").read_text())
    assert report["status"] == "runtime-error"
    assert report["per_target_metrics"]["ko"]["translation_coverage"] == 1
    assert report["per_target_metrics"]["en"]["translation_coverage"] == 0
    assert report["per_target_metrics"]["en"]["target_states"] == {"failed": 1}
    assert report["per_target_metrics"]["en"]["translated_hypothesis"] == ""
    assert report["queues_drained"]


def parallel_fixture(tmp_path):
    import hashlib
    import json
    import wave

    from scripts.evaluate_live_fleurs_translation import FLEURS_REVISION

    cohorts = {}
    for language in ("ko", "en", "ja"):
        pcm = b"\x01\x00" * 4000
        wav = tmp_path / f"{language}.wav"
        with wave.open(str(wav), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(pcm)
        text = f"GOLD_SENTINEL_{language}"
        cohorts[language] = [{
            "id": 1883, "source_language": language, "duration_seconds": 0.25,
            "reference": text, "reference_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "wav_path": wav.name, "sha256_wav": hashlib.sha256(wav.read_bytes()).hexdigest(),
            "sha256_pcm16": hashlib.sha256(pcm).hexdigest(),
        }]
    manifest = tmp_path / "manifest.json"
    payload = {"schema_version": 1, "dataset": "google/fleurs", "revision": FLEURS_REVISION,
               "license": "CC-BY-4.0", "common_sentence_ids": [1883], "cohorts": cohorts,
               "parallelism": {"scope": "synthetic validation fixture, no human translation claim"}}
    manifest.write_text(json.dumps(payload))
    return manifest, payload


def test_local_manifest_hashes_all_sources_and_target_references(tmp_path):
    import hashlib

    from scripts.evaluate_live_fleurs_translation import load_local_pinned_speech

    manifest, _ = parallel_fixture(tmp_path)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    pcm, clips, reference, provenance = load_local_pinned_speech(manifest, digest, "ja")
    assert len(pcm) == 8000
    assert clips[0]["source_language"] == "ja"
    assert reference == "GOLD_SENTINEL_ja"
    assert provenance["translation_references"] == {
        "ko": "GOLD_SENTINEL_ko", "en": "GOLD_SENTINEL_en"
    }
    assert "wav_path" not in clips[0]
    (tmp_path / "en.wav").write_bytes(b"corrupt foreign reference media")
    with pytest.raises(ValueError, match="WAV hash"):
        load_local_pinned_speech(manifest, digest, "ja")


@pytest.mark.parametrize("mutation", ["hash", "order", "reference", "path", "pin"])
def test_local_manifest_rejects_mismatched_inputs(tmp_path, mutation):
    import hashlib
    import json

    from scripts.evaluate_live_fleurs_translation import load_local_pinned_speech

    manifest, payload = parallel_fixture(tmp_path)
    if mutation == "order":
        payload["cohorts"]["en"][0]["id"] = 1929
    elif mutation == "reference":
        payload["cohorts"]["en"][0]["reference"] = "changed without updating digest"
    elif mutation == "path":
        payload["cohorts"]["en"][0]["wav_path"] = "../foreign.wav"
    elif mutation == "pin":
        payload["revision"] = "mutable-main"
    manifest.write_text(json.dumps(payload))
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest() if mutation != "hash" else "0" * 64
    with pytest.raises(ValueError):
        load_local_pinned_speech(manifest, digest, "ja")
