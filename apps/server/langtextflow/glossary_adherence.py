"""Provider-neutral, observational glossary adherence diagnostics.

Never mutate the translator output, force textual substitutions or claim that
a matched term proves semantic correctness of translated scripture/numerals.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import SessionContext


@dataclass(frozen=True)
class TerminologyMapping:
    source: str
    target: str
    aliases: tuple[str, ...]
    language: str
    category: str


@dataclass(frozen=True)
class TerminologyMiss:
    source: str
    expected: str
    category: str


@dataclass(frozen=True)
class GlossaryAdherence:
    target_language: str
    relevant_terms: int
    matched_terms: int
    adherence: float | None
    missing: tuple[TerminologyMiss, ...]
    evaluation_kind: str = "exact_lexical_observation_not_semantic_certification"


def resolve_glossary(
    context: SessionContext,
    target_language: str,
) -> tuple[TerminologyMapping, ...]:
    """Resolve enabled terms applicable to the current preset and language."""
    language = target_language.strip().casefold()
    seen: set[str] = set()
    mappings: list[TerminologyMapping] = []
    for entry in context.glossary:
        if not entry.enabled or not entry.applies_to(context.preset):
            continue
        translation = entry.translations.get(language)
        if not translation:
            continue
        key = entry.term.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        mappings.append(
            TerminologyMapping(
                source=entry.term,
                target=translation,
                aliases=tuple(entry.aliases),
                language=language,
                category=entry.category,
            )
        )
    return tuple(mappings)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _source_occurs(text: str, mapping: TerminologyMapping) -> bool:
    normalized = _fold(text)
    return any(_fold(term) in normalized for term in (mapping.source, *mapping.aliases))


def _translation_occurs(text: str, term: str, language: str) -> bool:
    """Exact lexical match; Japanese is a *substring signal*, not word segmentation."""
    expected = _fold(term).strip()
    if not expected:
        return False
    candidate = _fold(text)
    if language == "en":
        # Avoid credit for English inside another alphanumeric word:
        # 'cross' in 'crossing', 'faith' in 'unfaithful', etc.
        pattern = r"(?<![a-z0-9])" + re.escape(expected) + r"(?![a-z0-9])"
        return re.search(pattern, candidate) is not None
    # Japanese has no whitespace word boundary; do not infer morphology or semantics.
    return expected in candidate


def measure_glossary_adherence(
    source: str,
    translated: str,
    mappings: tuple[TerminologyMapping, ...],
    target_language: str,
) -> GlossaryAdherence:
    """Return an observational signal, without ever changing translated text.

    A missing/altered translation is flagged. An exact match alone does not prove
    semantic correctness, particularly for Japanese compounds and Bible verses.
    """
    language = target_language.strip().casefold()
    relevant = [
        mapping for mapping in mappings
        if mapping.language == language and _source_occurs(source, mapping)
    ]
    misses = tuple(
        TerminologyMiss(mapping.source, mapping.target, mapping.category)
        for mapping in relevant
        if not _translation_occurs(translated, mapping.target, language)
    )
    expected = len(relevant)
    matched = expected - len(misses)
    return GlossaryAdherence(
        target_language=language,
        relevant_terms=expected,
        matched_terms=matched,
        adherence=matched / expected if expected else None,
        missing=misses,
    )
