"""Versioned, opt-in Korean sermon terminology starter data.

These are EDITABLE Korean-canonical starter mappings; they are not validated
English/Japanese-source aliases for multilingual ASR (issue #130).
"""
from __future__ import annotations

import json
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
