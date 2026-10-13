"""Fail-closed, source-scoped glossary evaluation for CPU MT experiments.

Reads the JSON shape exported by GlossaryTransfer (PR #134), not an unrelated
research-only dictionary. No surface-string rewriting or claim of constrained
CTranslate2 decoding: lexical candidate reranking is experimental.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

LANGUAGES = frozenset({"ko", "en", "ja"})
MAX_RELEVANT = 24
MAX_GLOSSARY_ROWS = 5000

_NEGATION = {
    "en": re.compile(r"\b(?:not|no|never|cannot|without|neither|nor)\b|n't\b", re.I),
    "ko": re.compile(r"않|없|아니|못|결코|절대|안\s"),
    "ja": re.compile(r"ない|ません|できません|ありません|じゃない|ではない|ずに"),
}
_NUMBERS = re.compile(r"\d+(?:[.:/-]\d+)*")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _occurs(haystack: str, needle: str) -> bool:
    source = _normalize(haystack)
    term = _normalize(needle).strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9\s.'-]*", term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", source) is not None
    return term in source


def load_glossary_snapshot(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Validate a versioned operator export; never infer an unknown source language."""
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("glossary JSON schema_version must be 1")
    entries = document.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_GLOSSARY_ROWS:
        raise ValueError("glossary export requires 1..5000 entries")
    seen: set[tuple[str, str]] = set()
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("each glossary entry must be an object")
        lang = row.get("source_language")
        term = row.get("term")
        translations = row.get("translations")
        if lang not in LANGUAGES or not isinstance(term, str) or not term.strip():
            raise ValueError("explicit ko/en/ja glossary source_language and term required")
        if not isinstance(translations, dict) or not all(
            language in LANGUAGES and isinstance(value, str) and value.strip()
            for language, value in translations.items()
        ):
            raise ValueError("glossary translations must use supported target languages")
        aliases = row.get("aliases", [])
        presets = row.get("presets", [])
        if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
            raise ValueError("glossary aliases must be a string list")
        if not isinstance(presets, list) or not all(isinstance(x, str) for x in presets):
            raise ValueError("glossary presets must be a string list")
        if not isinstance(row.get("enabled", True), bool):
            raise ValueError("glossary enabled must be boolean")
        key = (lang, _normalize(term).strip())
        if key in seen:
            raise ValueError("duplicate glossary term for source language")
        seen.add(key)
    return entries, hashlib.sha256(raw).hexdigest()


def relevant_terms(
    entries: list[dict[str, Any]], source_text: str, source: str, target: str,
    *, preset: str = "church", limit: int = MAX_RELEVANT,
) -> list[dict[str, str]]:
    """Restrict aliases and targets to the selected session's spoken language."""
    if source not in LANGUAGES or target not in LANGUAGES or source == target:
        raise ValueError("invalid source/target glossary direction")
    if limit < 0 or limit > MAX_RELEVANT:
        raise ValueError("glossary relevance limit must be 0..24")
    matches = []
    for row in entries:
        if not row.get("enabled", True) or row["source_language"] != source:
            continue
        if row.get("presets") and preset not in row["presets"]:
            continue
        expected = row["translations"].get(target)
        if not expected:
            continue
        if any(_occurs(source_text, term) for term in (row["term"], *row.get("aliases", []))):
            matches.append({
                "source": row["term"], "target": expected,
                "boost": float(row.get("boost", 1.0)),
            })
    matches.sort(key=lambda x: (-len(x["source"]), -x["boost"], x["source"]))
    return [{"source": row["source"], "target": row["target"]} for row in matches[:limit]]


def matched_terms(text: str, mappings: list[dict[str, str]]) -> set[str]:
    return {
        mapping["source"] for mapping in mappings
        if _occurs(text, mapping["target"])
    }


def _protected_surface_unchanged(original: str, candidate: str, target: str) -> bool:
    """Cheap regression guard, NOT proof that the Bible reference/meaning is correct."""
    if _NUMBERS.findall(_normalize(original)) != _NUMBERS.findall(_normalize(candidate)):
        return False
    return bool(_NEGATION[target].search(original)) == bool(
        _NEGATION[target].search(candidate)
    )


def choose_candidate(
    baseline: str, hypotheses: list[str], source_text: str,
    mappings: list[dict[str, str]], target: str,
    *, safe: Callable[[str, str], bool],
) -> dict[str, Any]:
    """Prefer terminology-containing *generated* candidates, never substitute words.

    Preserve baseline terms, digit spellings, detectable negation, and a bounded
    length ratio. If none qualify, expose an unmet-glossary signal instead of
    pretending the dictionary was enforced.
    """
    if target not in LANGUAGES:
        raise ValueError("unsupported target language")
    base_hits = matched_terms(baseline, mappings)
    chosen = baseline
    hits = base_hits
    if mappings and safe(source_text, baseline):
        for hypothesis in hypotheses:
            if not safe(source_text, hypothesis):
                continue
            candidate_hits = matched_terms(hypothesis, mappings)
            if not base_hits.issubset(candidate_hits):
                continue
            if len(candidate_hits) <= len(hits):
                continue
            if not _protected_surface_unchanged(baseline, hypothesis, target):
                continue
            length_ratio = len(hypothesis) / max(len(baseline), 1)
            if not 0.70 <= length_ratio <= 1.30:
                continue
            chosen, hits = hypothesis, candidate_hits
    misses = [
        item["target"] for item in mappings if item["source"] not in hits
    ]
    return {
        "hypothesis": chosen,
        "baseline_hypothesis": baseline,
        "relevant": len(mappings),
        "baseline_matched": len(base_hits),
        "matched": len(hits),
        "missing_targets": misses,
        "adherence": len(hits) / len(mappings) if mappings else None,
        "applied": bool(chosen != baseline),
        "strategy": (
            "not_applicable" if not mappings
            else "candidate_rerank" if chosen != baseline
            else "no_safe_improvement"
        ),
        "semantic_reviewed": False,
        "glossary_guaranteed": False,
    }
