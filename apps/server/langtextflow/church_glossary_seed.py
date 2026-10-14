"""Versioned, opt-in Korean sermon terminology starter data.

These are EDITABLE Korean-canonical starter mappings; they are not validated
English/Japanese-source aliases for multilingual ASR (issue #130).
"""
from __future__ import annotations

import json
from collections import Counter
from importlib.resources import files

from .models import GlossaryEntry

_CHURCH_GLOSSARY_PATH = "data/church_glossary_seed.v1.json"


def load_church_glossary() -> list[GlossaryEntry]:
    source = files("langtextflow").joinpath(_CHURCH_GLOSSARY_PATH)
    dataset = json.loads(source.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1 or dataset.get("source_language") != "ko":
        raise ValueError("unsupported church starter glossary schema/source language")
    rows = dataset.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("church glossary entries must be a non-empty list")
    entries = [GlossaryEntry.model_validate(row) for row in rows]
    keys = [entry.term.casefold() for entry in entries]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate canonical church glossary terms")
    return entries


CHURCH_GLOSSARY: list[GlossaryEntry] = load_church_glossary()


def church_glossary_for_source(source_language: str) -> list[GlossaryEntry]:
    """Opt-in starter entries for the sermon *spoken* language.

    Reverse-maps only unambiguous manually-curated KO source translations.
    Foreign-source ASR aliases are deliberately NOT inferred. This is an
    editable dictionary proposal, not model quality or contextual validation.
    """
    if source_language == "ko":
        return [entry.model_copy(deep=True) for entry in CHURCH_GLOSSARY]
    if source_language not in {"en", "ja"}:
        raise ValueError("church starter is available for ko/en/ja only")
    other_language = "ja" if source_language == "en" else "en"
    counts = Counter(
        name.casefold()
        for entry in CHURCH_GLOSSARY
        if (name := entry.translations.get(source_language, "").strip())
    )
    rows: list[GlossaryEntry] = []
    for entry in CHURCH_GLOSSARY:
        source_term = entry.translations.get(source_language, "").strip()
        if not source_term or counts[source_term.casefold()] != 1:
            # E.g. multiple Korean expressions may map to one "baptism".
            # Never silently choose one and bias a foreign-source sermon.
            continue
        translations = {"ko": entry.term}
        other = entry.translations.get(other_language, "").strip()
        if other:
            translations[other_language] = other
        rows.append(GlossaryEntry(
            term=source_term,
            source_language=source_language,
            translations=translations,
            aliases=[],
            category=entry.category,
            presets=entry.presets.copy(),
            boost=entry.boost,
            enabled=entry.enabled,
        ))
    return rows
