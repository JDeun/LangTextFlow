"""License and real glossary gate regressions, stdlib-only."""
from __future__ import annotations

import copy
import json
import unittest

from scripts.check_model_candidate_catalog import (
    CATALOG,
    glossary_ablation_available,
    licensed_for_research,
    report,
    validate_catalog,
)


class CandidateCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(CATALOG.read_text(encoding="utf-8"))

    def test_real_catalog_passes_metadata_checks_not_model_quality(self) -> None:
        rows = report(self.data)
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["field_ready"] is False for row in rows))

    def test_three_language_missionary_directions(self) -> None:
        models = {item["model_id"]: item for item in self.data["models"]}
        m2m = models["facebook/m2m100_418M"]
        self.assertEqual(len(m2m["pairs"]), 6)
        opus = models["Helsinki-NLP/opus-mt-ko-en"]
        self.assertEqual(opus["pairs"], ["ko->en"])
        fun = models["FunAudioLLM/Fun-ASR-MLT-Nano-2512"]
        self.assertEqual(set(fun["source_languages"]), {"ko", "en", "ja"})

    def test_three_gemma_candidates_are_explicitly_registered(self) -> None:
        models = {m["model_id"]: m for m in self.data["models"]}
        expected = {
            "google/gemma-3-1b-it",
            "google/gemma-4-E2B-it",
            "google/translategemma-4b-it",
        }
        self.assertTrue(expected <= set(models))
        for name in expected:
            self.assertEqual(set(models[name]["pairs"]), set(self.data["language_priority"]))
            self.assertFalse(glossary_ablation_available(models[name]))
            self.assertEqual(models[name]["quality_evidence"], "not_measured")
        self.assertEqual(models["google/gemma-4-E2B-it"]["kind"], "speech_translation")
        self.assertEqual(
            set(models["google/gemma-4-E2B-it"]["input_modalities"]),
            {"text", "audio"},
        )
        self.assertEqual(models["google/gemma-4-E2B-it"]["audio_max_seconds"], 30)

    def test_gemma_terms_are_not_silently_treated_as_apache_licensed(self) -> None:
        models = {m["model_id"]: m for m in self.data["models"]}
        for name in ("google/gemma-3-1b-it", "google/translategemma-4b-it"):
            model = models[name]
            self.assertEqual(model["license"]["id"], "gemma")
            self.assertEqual(model["license"]["status"], "terms_review_required")
            self.assertFalse(licensed_for_research(model))
            self.assertTrue(model["execution"].startswith("blocked_"))
        self.assertTrue(licensed_for_research(models["google/gemma-4-E2B-it"]))

    def test_unreviewed_gemma_cannot_be_marked_executable(self) -> None:
        changed = copy.deepcopy(self.data)
        row = next(m for m in changed["models"] if m["model_id"] == "google/gemma-3-1b-it")
        row["execution"] = "planned_cpu_experiment"
        with self.assertRaisesRegex(ValueError, "blocked license"):
            validate_catalog(changed)

    def test_gemma_4_audio_limit_and_modality_are_required(self) -> None:
        for invalid in ([], ["text"], ["audio"], ["audio", "text", "video"]):
            changed = copy.deepcopy(self.data)
            row = next(m for m in changed["models"] if m["model_id"] == "google/gemma-4-E2B-it")
            row["input_modalities"] = invalid
            with self.assertRaisesRegex(ValueError, "bounded input duration"):
                validate_catalog(changed)
        changed = copy.deepcopy(self.data)
        row = next(m for m in changed["models"] if m["model_id"] == "google/gemma-4-E2B-it")
        row["audio_max_seconds"] = 31
        with self.assertRaisesRegex(ValueError, "bounded input duration"):
            validate_catalog(changed)

    def test_unknown_or_korea_excluded_models_are_blocked(self) -> None:
        models = {item["model_id"]: item for item in self.data["models"]}
        for name in (
            "facebook/mbart-large-50-many-to-many-mmt",
            "tencent/HY-MT1.5-1.8B",
        ):
            self.assertFalse(licensed_for_research(models[name]))
            self.assertFalse(glossary_ablation_available(models[name]))

    def test_license_misrepresentation_fails_closed(self) -> None:
        changed = copy.deepcopy(self.data)
        restricted = next(m for m in changed["models"] if m["model_id"].startswith("tencent/"))
        restricted["execution"] = "planned_cpu_experiment"
        with self.assertRaisesRegex(ValueError, "blocked license"):
            validate_catalog(changed)

    def test_glossary_contract_cannot_be_disabled(self) -> None:
        changed = copy.deepcopy(self.data)
        changed["validation_contract"]["requires_same_audio_and_glossary_snapshot"] = False
        with self.assertRaisesRegex(ValueError, "mandatory"):
            validate_catalog(changed)

    def test_direction_mismatch_fails_closed(self) -> None:
        changed = copy.deepcopy(self.data)
        opus = next(m for m in changed["models"] if m["model_id"].endswith("opus-mt-ko-en"))
        opus["pairs"] = ["ja->ko"]
        with self.assertRaisesRegex(ValueError, "unsupported source"):
            validate_catalog(changed)

    def test_license_and_glossary_wiring_are_separate(self) -> None:
        models = {item["model_id"]: item for item in self.data["models"]}
        self.assertTrue(licensed_for_research(models["google/madlad400-3b-mt"]))
        self.assertFalse(glossary_ablation_available(models["google/madlad400-3b-mt"]))
        # The real intervention is on unmerged stacked PR #135, NOT this branch.
        self.assertFalse(glossary_ablation_available(models["facebook/m2m100_418M"]))


if __name__ == "__main__":
    unittest.main()
