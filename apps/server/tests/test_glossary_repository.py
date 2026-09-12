from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import GlossaryEntry
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
        )
    )
    assert database.exists()
    assert repository.get(created.id) is not None
    assert repository.list()[0].translations["en"] == "justification"

    updated = repository.update(
        created.id,
        GlossaryEntry(
            term="칭의",
            aliases=["칭이", "칭위"],
            translations={"en": "justification"},
            category="theology",
            enabled=False,
        ),
    )
    assert updated is not None
    assert updated.enabled is False
    assert updated.aliases == ["칭이", "칭위"]

    assert repository.delete(created.id) is True
    assert repository.get(created.id) is None


def test_church_seed_is_idempotent(tmp_path) -> None:
    repository = GlossaryRepository(str(tmp_path / "langtextflow.db"))
    repository.initialize()

    first = repository.seed(CHURCH_GLOSSARY)
    second = repository.seed(CHURCH_GLOSSARY)

    assert len(first) == len(CHURCH_GLOSSARY)
    assert len(second) == len(CHURCH_GLOSSARY)
    assert {record.term for record in second} >= {"요한복음", "로마서", "칭의", "성화"}
