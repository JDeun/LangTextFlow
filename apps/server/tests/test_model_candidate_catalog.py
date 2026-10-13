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
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row["field_ready"] is False for row in rows))

    def test_three_language_missionary_directions(self) -> None:
        models = {item["model_id"]: item for item in self.data["models"]}
        m2m = models["facebook/m2m100_418M"]
        self.assertEqual(len(m2m["pairs"]), 6)
        opus = models["Helsinki-NLP/opus-mt-ko-en"]
        self.assertEqual(opus["pairs"], ["ko->en"])
        fun = models["FunAudioLLM/Fun-ASR-MLT-Nano-2512"]
        self.assertEqual(set(fun["source_languages"]), {"ko", "en", "ja"})

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
