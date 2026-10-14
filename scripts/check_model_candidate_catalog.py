"""Deterministic CPU candidate eligibility checker; NO model download/inference.

A green catalog check only proves metadata discipline, not licenses beyond the
linked model-card claims, training data suitability or any model's accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CATALOG = Path("benchmarks/ko_en_ja_model_candidates.v1.json")
LANGUAGES = frozenset({"ko", "en", "ja"})
PAIRS = frozenset(
    f"{source}->{target}"
    for source in LANGUAGES for target in LANGUAGES if source != target
)
PERMISSIVE = frozenset({"apache-2.0", "mit"})
VERIFIED = "model_card_verified"


def validate_catalog(document: dict[str, Any]) -> None:
    if document.get("schema_version") != 1:
        raise ValueError("unsupported model catalog schema")
    if set(document.get("language_priority", [])) != PAIRS:
        raise ValueError("catalog must cover all six directions")
    contract = document.get("validation_contract")
    if not isinstance(contract, dict) or not all(
        contract.get(key) is True
        for key in (
            "no_production_ranking_without_glossary_ablation",
            "requires_same_audio_and_glossary_snapshot",
            "no_inference_of_streaming_from_utterance_replay",
            "model_revision_sha_required_at_run",
            "license_review_before_download",
        )
    ):
        raise ValueError("mandatory glossary/provenance gates may not be disabled")
    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("at least one candidate is required")
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("candidate must be an object")
        identifier = model.get("model_id")
        if not isinstance(identifier, str) or "/" not in identifier:
            raise ValueError("candidate requires an explicit model ID")
        if identifier in seen:
            raise ValueError(f"duplicate model ID: {identifier}")
        seen.add(identifier)
        kind = model.get("kind")
        if kind not in {"asr", "mt", "speech_translation"}:
            raise ValueError(f"{identifier}: unrecognized model kind")
        source_languages = model.get("source_languages")
        if (not isinstance(source_languages, list) or not source_languages
                or not set(source_languages) <= LANGUAGES
                or len(source_languages) != len(set(source_languages))):
            raise ValueError(f"{identifier}: invalid source language scope")
        declared_pairs = model.get("pairs")
        if not isinstance(declared_pairs, list):
            raise ValueError(f"{identifier}: pairs list required")
        if kind == "asr" and declared_pairs:
            raise ValueError(f"{identifier}: ASR does not translate")
        if kind in {"mt", "speech_translation"}:
            if not declared_pairs or not set(declared_pairs) <= PAIRS:
                raise ValueError(f"{identifier}: invalid translation pairs")
            if len(declared_pairs) != len(set(declared_pairs)):
                raise ValueError(f"{identifier}: duplicate translation directions")
            if any(pair.split("->")[0] not in source_languages
                   for pair in declared_pairs):
                raise ValueError(f"{identifier}: pair with unsupported source")
        if kind == "speech_translation":
            modalities = model.get("input_modalities")
            if (not isinstance(modalities, list)
                    or set(modalities) != {"audio", "text"}
                    or not isinstance(model.get("audio_max_seconds"), int)
                    or not 1 <= model["audio_max_seconds"] <= 30):
                raise ValueError(f"{identifier}: audio model needs bounded input duration")
        license_data = model.get("license")
        if not isinstance(license_data, dict):
            raise ValueError(f"{identifier}: license evidence is required")
        status = license_data.get("status")
        license_id = license_data.get("id")
        territory = license_data.get("territory")
        url = license_data.get("evidence_url")
        if not isinstance(url, str) or not url.startswith("https://huggingface.co/"):
            raise ValueError(f"{identifier}: license evidence URL required")
        if status not in {
            VERIFIED, "pending_explicit_permission", "territory_excluded",
            "terms_review_required", "noncommercial_research_only",
        }:
            raise ValueError(f"{identifier}: unsupported license status")
        if status == VERIFIED:
            if license_id not in PERMISSIVE or territory != "permissive":
                raise ValueError(f"{identifier}: verified license must be permissive")
        elif status == "pending_explicit_permission":
            if territory != "unknown" or license_id != "unknown":
                raise ValueError(f"{identifier}: unknown license must block execution")
        elif status == "terms_review_required":
            if license_id != "gemma" or territory != "terms_review":
                raise ValueError(f"{identifier}: Gemma terms must be explicitly reviewed")
        elif status == "noncommercial_research_only":
            if license_id != "cc-by-nc-4.0" or territory != "noncommercial_only":
                raise ValueError(f"{identifier}: noncommercial use must be disclosed")
        elif territory != "south_korea_excluded":
            raise ValueError(f"{identifier}: territory exclusion must be explicit")
        execution = model.get("execution")
        glossary = model.get("glossary")
        if not isinstance(execution, str) or not isinstance(glossary, str):
            raise ValueError(f"{identifier}: execution and glossary evidence required")
        if status != VERIFIED and not execution.startswith(
            ("blocked_", "excluded_")
        ):
            raise ValueError(f"{identifier}: blocked license cannot enter experiment")
        if kind in {"mt", "speech_translation"} and not glossary:
            raise ValueError(f"{identifier}: MT glossary handling must be declared")


def licensed_for_research(model: dict[str, Any]) -> bool:
    """Admission gate for Korean CPU experiments, NOT a final legal opinion."""
    license_data = model["license"]
    return (
        license_data["status"] == VERIFIED
        and license_data["id"] in PERMISSIVE
        and license_data["territory"] == "permissive"
    )


def glossary_ablation_available(model: dict[str, Any]) -> bool:
    """True only for a wired experimental implementation, not a quality pass."""
    return (
        licensed_for_research(model)
        and model["kind"] == "mt"
        and model["glossary"] == "experimental_beam4_candidate_rerank_not_constrained"
    )


def report(document: dict[str, Any]) -> list[dict[str, Any]]:
    validate_catalog(document)
    return [
        {
            "model_id": model["model_id"],
            "kind": model["kind"],
            "directions": model["pairs"] if model["kind"] in {
                "mt", "speech_translation"
            } else model["source_languages"],
            "license_research_eligible": licensed_for_research(model),
            "glossary_ablation_wired": glossary_ablation_available(model),
            "execution": model["execution"],
            "field_ready": False,
        }
        for model in document["models"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.catalog.read_text(encoding="utf-8"))
    rows = report(document)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(
            f"{row['model_id']}: license_ok={row['license_research_eligible']} "
            f"glossary_ab_wired={row['glossary_ablation_wired']} "
            f"execution={row['execution']}"
        )
    print("Metadata validated; no model executed or quality-certified.")


if __name__ == "__main__":
    main()
