"""Validate curated sermon translation anchors without requiring model weights."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

LANGUAGES = {"ko", "en", "ja"}
CORPUS = Path(__file__).resolve().parents[1] / "benchmarks" / "sermon_mt_eval.jsonl"


def validate(path: Path = CORPUS) -> dict[str, object]:
    ids: set[str] = set()
    pairs: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        record = json.loads(raw)
        assert isinstance(record, dict), f"line {line_number}: object required"
        assert set(record) == {
            "id", "source_language", "target_language", "source", "reference", "category",
        }, f"line {line_number}: unexpected schema"
        identifier = record["id"]
        assert isinstance(identifier, str) and identifier not in ids
        ids.add(identifier)
        source, target = record["source_language"], record["target_language"]
        assert source in LANGUAGES and target in LANGUAGES and source != target
        for key in ("source", "reference", "category"):
            assert isinstance(record[key], str) and record[key].strip()
        pairs[f"{source}->{target}"] += 1
        categories[record["category"]] += 1
    expected = {f"{s}->{t}" for s in LANGUAGES for t in LANGUAGES if s != t}
    assert set(pairs) == expected, f"missing directions: {sorted(expected - set(pairs))}"
    return {"samples": len(ids), "pairs": dict(sorted(pairs.items())),
            "categories": dict(sorted(categories.items()))}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
