import json
from datetime import UTC, datetime

import pytest

from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.glossary_transfer import (
    MAX_GLOSSARY_IMPORT_ENTRIES,
    GlossaryConflictPolicy,
    GlossaryImportRequest,
    GlossaryTransferFormat,
    export_glossary,
    parse_glossary_import,
)
from langtextflow.models import GlossaryEntry, GlossaryRecord, ProductPreset


def record() -> GlossaryRecord:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    return GlossaryRecord(
        id="record-1",
        term="칭의",
        aliases=["칭이"],
        translations={"en": "justification", "ja": "義認"},
        category="theology",
        presets=[ProductPreset.CHURCH],
        boost=1.75,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def test_json_export_is_portable_and_round_trips() -> None:
    content = export_glossary([record()], GlossaryTransferFormat.JSON)
    assert '"schema_version": 1' in content
    assert '"id"' not in content
    assert '"created_at"' not in content

    entries = parse_glossary_import(
        GlossaryImportRequest(format="json", content=content)
    )

    assert len(entries) == 1
    assert entries[0].term == "칭의"
    assert entries[0].translations["en"] == "justification"
    assert entries[0].presets == [ProductPreset.CHURCH]
    assert entries[0].boost == 1.75


def test_csv_export_uses_bom_and_round_trips_json_cells() -> None:
    content = export_glossary([record()], GlossaryTransferFormat.CSV)
    assert content.startswith("\ufeffterm,")

    entries = parse_glossary_import(
        GlossaryImportRequest(format="csv", content=content)
    )

    assert entries[0].aliases == ["칭이"]
    assert entries[0].translations == {"en": "justification", "ja": "義認"}
    assert entries[0].enabled is True


def test_csv_export_neutralizes_formula_cells_and_round_trips() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)
    hostile = GlossaryRecord(
        id="record-hostile",
        term="=HYPERLINK(\"https://example.invalid\")",
        aliases=[],
        translations={},
        category="@SUM(1+1)",
        presets=[ProductPreset.GENERAL],
        boost=1.0,
        enabled=True,
        created_at=now,
        updated_at=now,
    )

    content = export_glossary([hostile], GlossaryTransferFormat.CSV)
    assert "'=HYPERLINK" in content
    assert "'@SUM(1+1)" in content

    entries = parse_glossary_import(
        GlossaryImportRequest(format="csv", content=content)
    )
    assert entries[0].term == hostile.term
    assert entries[0].category == hostile.category


def test_import_rejects_excessive_entry_count_before_validation() -> None:
    content = json.dumps(
        [{"term": f"term-{index}"} for index in range(MAX_GLOSSARY_IMPORT_ENTRIES + 1)]
    )

    with pytest.raises(ValueError, match="entry limit"):
        parse_glossary_import(GlossaryImportRequest(format="json", content=content))


def test_import_rejects_duplicate_terms_case_insensitively() -> None:
    content = """
    {
      "entries": [
        {"term": "OpenAI"},
        {"term": "openai"}
      ]
    }
    """

    with pytest.raises(ValueError, match="duplicate term"):
        parse_glossary_import(GlossaryImportRequest(format="json", content=content))


def test_import_rejects_malformed_csv_before_repository_write(tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create(GlossaryEntry(term="existing"))
    before = repository.list()

    malformed = "term,aliases_json\nnew-term,not-json\n"
    with pytest.raises(ValueError, match="aliases_json"):
        parse_glossary_import(GlossaryImportRequest(format="csv", content=malformed))

    assert repository.list() == before


def test_repository_upsert_preserves_identity_and_skip_policy(tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    original = repository.create(
        GlossaryEntry(
            term="OpenAI",
            aliases=["old"],
            translations={"ko": "오픈에이아이"},
        )
    )

    entries = [
        GlossaryEntry(
            term="openai",
            aliases=["new"],
            translations={"ko": "오픈AI"},
            category="organization",
        ),
        GlossaryEntry(term="RAG", category="technical"),
    ]
    created, updated, skipped = repository.import_entries(entries, conflict_policy="upsert")

    assert (created, updated, skipped) == (1, 1, 0)
    current = next(item for item in repository.list() if item.term.casefold() == "openai")
    assert current.id == original.id
    assert current.created_at == original.created_at
    assert current.aliases == ["new"]
    assert current.category == "organization"

    created, updated, skipped = repository.import_entries(
        [GlossaryEntry(term="OPENAI", aliases=["ignored"])],
        conflict_policy=GlossaryConflictPolicy.SKIP.value,
    )
    assert (created, updated, skipped) == (0, 0, 1)
    unchanged = repository.get(original.id)
    assert unchanged is not None
    assert unchanged.aliases == ["new"]
