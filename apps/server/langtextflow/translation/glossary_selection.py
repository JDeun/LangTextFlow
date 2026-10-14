"""Select only utterance-relevant terms: broad default sets must not inflate CPU MT."""
from __future__ import annotations

import re
import unicodedata

from ..models import GlossaryEntry, SessionContext

MAX_TRANSLATION_GLOSSARY_HINTS = 24


def _mentioned(source: str, term: str) -> bool:
    normalized = unicodedata.normalize("NFKC", source).casefold()
    phrase = unicodedata.normalize("NFKC", term).casefold().strip()
    if not phrase:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9\s.'-]*", phrase):
        pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
        return re.search(pattern, normalized) is not None
    return phrase in normalized


def relevant_glossary(
    context: SessionContext,
    source_text: str,
    target_language: str,
    *,
    source_language: str = "ko",
    limit: int = MAX_TRANSLATION_GLOSSARY_HINTS,
) -> list[tuple[GlossaryEntry, str]]:
    """Source-scoped lexical hints, not forced decoding or quality certification.

    The Korean default supports legacy direct calls; every production adapter
    must pass its actual source language explicitly.
    """
    if limit < 0:
        raise ValueError("glossary hint limit cannot be negative")
    if not source_text.strip() or not limit:
        return []
    source = source_language.strip().lower().replace("_", "-")
    matches: list[tuple[GlossaryEntry, str]] = []
    for entry in context.glossary:
        target = entry.translations.get(target_language)
        if (not entry.enabled or entry.source_language != source
                or not entry.applies_to(context.preset) or not target):
            continue
        if any(_mentioned(source_text, term) for term in (entry.term, *entry.aliases)):
            matches.append((entry, target))
    matches.sort(key=lambda pair: (-len(pair[0].term), -pair[0].boost, pair[0].term))
    return matches[:limit]
