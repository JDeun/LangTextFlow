from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import (
    GlossaryEntry,
    ProductPreset,
    SessionContext,
    StartSessionRequest,
)


def test_saved_glossary_is_scoped_and_snapshotted(monkeypatch, tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create(
        GlossaryEntry(
            term="칭의",
            translations={"en": "justification"},
            presets=[ProductPreset.CHURCH],
        )
    )
    repository.create(
        GlossaryEntry(term="RAG", presets=[ProductPreset.CONFERENCE])
    )
    monkeypatch.setattr(main, "glossary_repository", repository)

    request = StartSessionRequest(
        context=SessionContext(preset=ProductPreset.CHURCH)
    )
    merged = main._with_saved_glossary(request)

    assert [entry.term for entry in merged.context.glossary] == ["칭의"]
    assert merged.context.glossary[0].translations["en"] == "justification"


def test_explicit_session_glossary_overrides_saved_entry(monkeypatch, tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create(
        GlossaryEntry(
            term="복음",
            translations={"en": "gospel"},
            presets=[ProductPreset.CHURCH],
        )
    )
    monkeypatch.setattr(main, "glossary_repository", repository)

    request = StartSessionRequest(
        context=SessionContext(
            preset=ProductPreset.CHURCH,
            glossary=[
                GlossaryEntry(
                    term="복음",
                    translations={"en": "the gospel"},
                    presets=[ProductPreset.CHURCH],
                )
            ],
        )
    )
    merged = main._with_saved_glossary(request)

    assert len(merged.context.glossary) == 1
    assert merged.context.glossary[0].translations["en"] == "the gospel"
