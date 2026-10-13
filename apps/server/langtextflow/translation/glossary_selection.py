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
    limit: int = MAX_TRANSLATION_GLOSSARY_HINTS,
) -> list[tuple[GlossaryEntry, str]]:
    """Observational lexical match only; source-language scope needs #130."""
    if limit < 0:
        raise ValueError("glossary hint limit cannot be negative")
    if not source_text.strip() or not limit:
        return []
    matches: list[tuple[GlossaryEntry, str]] = []
    for entry in context.glossary:
        target = entry.translations.get(target_language)
        if not entry.enabled or not entry.applies_to(context.preset) or not target:
            continue
        if any(_mentioned(source_text, term) for term in (entry.term, *entry.aliases)):
            matches.append((entry, target))
    matches.sort(key=lambda pair: (-len(pair[0].term), -pair[0].boost, pair[0].term))
    return matches[:limit]
