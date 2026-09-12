from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import GlossaryEntry, ProductPreset
from langtextflow.presets import CHURCH_GLOSSARY


def test_glossary_crud_persists_to_sqlite(tmp_path) -> None:
    database = tmp_path / "langtextflow.db"
    repository = GlossaryRepository(str(database))
    repository.initialize()

    created = repository.create(
        GlossaryEntry(
            term="칭의",
            aliases=["칭이"],
            translations={"en": "justification"},
            category="theology",
            presets=[ProductPreset.CHURCH],
        )
    )
    assert database.exists()
    assert repository.get(created.id) is not None
    assert repository.list()[0].translations["en"] == "justification"
    assert repository.list()[0].presets == [ProductPreset.CHURCH]

    updated = repository.update(
        created.id,
        GlossaryEntry(
            term="칭의",
            aliases=["칭이", "칭위"],
            translations={"en": "justification"},
            category="theology",
            presets=[ProductPreset.CHURCH],
            enabled=False,
        ),
    )
    assert updated is not None
    assert updated.enabled is False
    assert updated.aliases == ["칭이", "칭위"]

    assert repository.delete(created.id) is True
    assert repository.get(created.id) is None


def test_church_seed_is_idempotent_and_scoped(tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()

    first = repository.seed(CHURCH_GLOSSARY)
    second = repository.seed(CHURCH_GLOSSARY)

    assert len(first) == len(CHURCH_GLOSSARY)
    assert len(second) == len(CHURCH_GLOSSARY)
    assert {record.term for record in second} >= {"요한복음", "로마서", "칭의", "성화"}
    assert all(record.presets == [ProductPreset.CHURCH] for record in second)


def test_active_for_returns_global_and_matching_preset_only(tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()
    repository.create(GlossaryEntry(term="OpenAI", category="organization"))
    repository.create(
        GlossaryEntry(term="칭의", category="theology", presets=[ProductPreset.CHURCH])
    )
    repository.create(
        GlossaryEntry(
            term="RAG",
            category="technical",
            presets=[ProductPreset.CONFERENCE],
        )
    )
    repository.create(
        GlossaryEntry(
            term="disabled",
            enabled=False,
            presets=[ProductPreset.CHURCH],
        )
    )

    church_terms = {entry.term for entry in repository.active_for(ProductPreset.CHURCH)}
    conference_terms = {
        entry.term for entry in repository.active_for(ProductPreset.CONFERENCE)
    }

    assert church_terms == {"OpenAI", "칭의"}
    assert conference_terms == {"OpenAI", "RAG"}
