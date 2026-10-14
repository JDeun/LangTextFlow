"""Conveyor summary must refuse skipped/failed stages and absent artifacts."""
from __future__ import annotations

from scripts.summarize_experiment_conveyor import EXPECTED, markdown, summarize


def _needs(profile: str) -> dict:
    return {
        stage: {"result": "success" if stage in EXPECTED[profile] else "skipped"}
        for stage in ("catalog", "m2m_cpu", "church_quality", "church_live", "apple")
    }


def test_metadata_schedule_never_marks_untested_models_passed() -> None:
    report = summarize("metadata", _needs("metadata"), set())
    assert report["execution_complete"]
    assert report["translation_quality_certified"] is False
    assert report["glossary_effectiveness_certified"] is False
    assert report["sermon_field_ready"] is False
    assert "not certified" in markdown(report)


def test_cpu_stage_needs_both_real_and_synthetic_evidence() -> None:
    names = {"text-six-directions.json", "real-audio-six-directions.json"}
    assert summarize("cpu", _needs("cpu"), names)["execution_complete"]
    missing = summarize("cpu", _needs("cpu"), {"text-six-directions.json"})
    assert not missing["execution_complete"]
    assert any("real-audio" in item for item in missing["problems"])


def test_mac_profile_only_uses_metadata_and_hosted_apple_probe() -> None:
    needs = _needs("mac")
    report = summarize("mac", needs, {"apple-language-availability.json"})
    assert report["execution_complete"]
    assert report["apple_translation_executed"] is False
    missing = summarize("mac", needs, set())
    assert any("apple-language-availability" in p for p in missing["problems"])


def test_full_profile_requires_apple_and_church_reports() -> None:
    artifacts = {
        "text-six-directions.json", "real-audio-six-directions.json",
        "church-translation-quality.json", "live-interpretation-report.json",
        "apple-language-availability.json",
    }
    report = summarize("full", _needs("full"), artifacts)
    assert report["execution_complete"]
    assert report["apple_translation_executed"] is False
    failed = _needs("full")
    failed["church_live"]["result"] = "failure"
    assert not summarize("full", failed, artifacts)["execution_complete"]


def test_skipped_expensive_stage_cannot_be_accidentally_counted() -> None:
    needs = _needs("cpu")
    needs["m2m_cpu"]["result"] = "skipped"
    report = summarize("cpu", needs, set())
    assert any("required result" in p for p in report["problems"])
    needs = _needs("metadata")
    needs["church_quality"]["result"] = "success"
    report = summarize("metadata", needs, set())
    assert any("unexpected execution" in p for p in report["problems"])
