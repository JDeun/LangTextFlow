"""Canonical identities and overlap rules for the shared global/preset glossary."""
from __future__ import annotations

from collections.abc import Sequence

from .models import GlossaryEntry, ProductPreset


def glossary_identity(entry: GlossaryEntry) -> tuple[str, str, tuple[str, ...]]:
    return (
        entry.source_language,
        entry.term.casefold(),
        tuple(sorted(preset.value for preset in entry.presets)),
    )


def glossary_scopes_conflict(
    left: Sequence[ProductPreset], right: Sequence[ProductPreset],
) -> bool:
    # An empty scope is the global layer, not a wildcard conflict with presets.
    return (not left and not right) or bool(set(left) & set(right))


def validate_scoped_import_entries(entries: Sequence[GlossaryEntry]) -> None:
    seen: dict[tuple[str, str], list[GlossaryEntry]] = {}
    for entry in entries:
        key = (entry.source_language, entry.term.casefold())
        for previous in seen.get(key, []):
            if glossary_identity(previous) == glossary_identity(entry):
                raise ValueError(f"duplicate term in import: {entry.source_language}:{entry.term}")
            if glossary_scopes_conflict(previous.presets, entry.presets):
                raise ValueError(
                    f"overlapping preset scopes in import: {entry.source_language}:{entry.term}"
                )
        seen.setdefault(key, []).append(entry)
