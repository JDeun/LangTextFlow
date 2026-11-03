"""Canonical evidence for an operator's resolved session glossary review."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field

from .models import MAX_GLOSSARY_ENTRIES, GlossaryEntry, ProductPreset, StartSessionRequest


class ResolvedGlossarySnapshot(BaseModel):
    source_language: str
    preset: ProductPreset
    project_id: str | None
    entries: list[GlossaryEntry] = Field(max_length=MAX_GLOSSARY_ENTRIES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def glossary_snapshot(request: StartSessionRequest) -> ResolvedGlossarySnapshot:
    entries = sorted(
        request.context.glossary,
        key=lambda entry: (
            entry.source_language,
            entry.term.casefold(),
            entry.term,
        ),
    )
    canonical = {
        "source_language": request.source_language,
        "preset": request.context.preset.value,
        "project_id": request.project_id,
        "entries": [entry.model_dump(mode="json") for entry in entries],
    }
    # Preset applicability is a set; JSON object key and repository row order
    # cannot change the digest for otherwise identical evidence.
    for entry in canonical["entries"]:
        entry["presets"] = sorted(entry["presets"])
    digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ResolvedGlossarySnapshot(**canonical, sha256=digest)
