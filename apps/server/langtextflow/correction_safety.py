"""Lexical correction guards, not semantic or morphological certification."""

import re
import unicodedata
from difflib import SequenceMatcher

_NUMBER_PATTERN = re.compile(r"\d+(?:[.,:/-]\d+)*")
_NEGATION = {
    "en": re.compile(r"\b(?:not|no|never|cannot|without|neither|nor)\b|n't\b"),
    "ko": re.compile(r"않|없|아니|못|결코|절대|(?<![가-힣])안(?=\s)"),
    "ja": re.compile(r"ない|ません|ずに|ぬ(?=$|[\s、。！？.,!?])"),
}


def numeric_tokens(text: str) -> list[str]:
    return _NUMBER_PATTERN.findall(text)


def _negations(text: str, language: str) -> tuple[str, list[tuple[int, int]]]:
    source = language.strip().lower().replace("_", "-").split("-")[0]
    pattern = _NEGATION.get(source)
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    if source == "en":
        normalized = normalized.replace("can't", "cannot").replace("won't", "will not")
        normalized = re.sub(r"(?<=\w)n't\b", " not", normalized)
    if pattern is None:
        return normalized, []
    return normalized, [match.span() for match in pattern.finditer(normalized)]


def protected_tokens(text: str, language: str) -> tuple[list[str], int]:
    # Counts retain separate negative clauses, unlike a whole-sentence boolean.
    return numeric_tokens(text), len(_negations(text, language)[1])


def preserves_alias_tokens(alias: str, canonical: str, language: str) -> bool:
    if protected_tokens(alias, language) != protected_tokens(canonical, language):
        return False
    original, old_spans = _negations(alias, language)
    candidate, new_spans = _negations(canonical, language)
    if not old_spans:
        return True
    def placement(text: str, spans: list[tuple[int, int]]) -> list[tuple[bool, bool]]:
        return [(bool(re.search(r"\w", text[:start])), bool(re.search(r"\w", text[end:])))
                for start, end in spans]

    if placement(original, old_spans) != placement(candidate, new_spans):
        return False
    # Alias/canonical fields are bounded to 160 characters. Never align the full transcript.
    for kind, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, original, candidate, autojunk=False,
    ).get_opcodes():
        if kind == "equal":
            continue
        if (any(old_start < end and old_end > start for start, end in old_spans)
                or any(new_start < end and new_end > start for start, end in new_spans)):
            return False
    return True
