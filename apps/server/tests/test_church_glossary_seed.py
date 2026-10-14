"""Versioned Korean-source church seed coverage and data safety."""
from __future__ import annotations

from langtextflow.church_glossary_seed import CHURCH_GLOSSARY, load_church_glossary
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import ProductPreset


def test_all_66_canonical_bible_books_have_en_and_ja_mappings() -> None:
    books = [entry for entry in CHURCH_GLOSSARY if entry.category == "bible-book"]
    assert len(books) == 66
    assert len(CHURCH_GLOSSARY) > 100
    assert len({entry.term.casefold() for entry in CHURCH_GLOSSARY}) == len(CHURCH_GLOSSARY)
    assert all(entry.presets == [ProductPreset.CHURCH] for entry in CHURCH_GLOSSARY)
    assert all(entry.enabled for entry in CHURCH_GLOSSARY)
    assert all(entry.translations.get("en") and entry.translations.get("ja")
               for entry in CHURCH_GLOSSARY)
    assert [entry.term for entry in books][:5] == [
        "창세기", "출애굽기", "레위기", "민수기", "신명기"
    ]
    assert [entry.term for entry in books][-1] == "요한계시록"


def test_existing_eight_terms_keep_legacy_english_and_aliases() -> None:
    expected = {
        "요한복음": ("Gospel of John", "ヨハネによる福音書"),
        "로마서": ("Romans", "ローマの信徒への手紙"),
        "성령": ("Holy Spirit", "聖霊"),
        "칭의": ("justification", "義認"),
        "성화": ("sanctification", "聖化"),
        "속죄": ("atonement", "贖罪"),
        "십자가": ("cross", "十字架"),
        "복음": ("gospel", "福音"),
    }
    found = {entry.term: entry for entry in load_church_glossary()}
    for term, (en, ja) in expected.items():
        assert found[term].translations == {"en": en, "ja": ja}
    assert found["요한복음"].aliases == ["요한 보금", "요한 복음"]
    assert found["칭의"].aliases == ["칭이", "칭위"]


def test_default_seed_remains_opt_in_idempotent_and_never_overwrites(tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "glossary.db"))
    repo.initialize()
    assert repo.list() == []  # Never auto-fill non-church/general workspaces.
    first = repo.seed(CHURCH_GLOSSARY)
    expected_count = len(CHURCH_GLOSSARY)
    assert len(first) == expected_count
    original = next(item for item in first if item.term == "복음")
    modified = original.model_copy(
        update={"translations": {"en": "the gospel", "ja": "福音の知らせ"}}
    )
    repo.update(original.id, modified)
    second = repo.seed(CHURCH_GLOSSARY)
    assert len(second) == expected_count
    after = repo.get(original.id)
    assert after is not None
    assert after.id == original.id
    assert after.created_at == original.created_at
    assert after.translations == {"en": "the gospel", "ja": "福音の知らせ"}
    assert len(repo.active_for(ProductPreset.CONFERENCE)) == 0
    assert len(repo.active_for(ProductPreset.CHURCH)) == expected_count


def test_foreign_church_seed_is_explicit_source_scoped_and_unambiguous(tmp_path) -> None:
    from langtextflow.church_glossary_seed import church_glossary_for_source

    en = church_glossary_for_source("en")
    ja = church_glossary_for_source("ja")
    assert len(en) == 151  # Reject three ambiguous EN labels (6 rows).
    assert len(ja) == 153  # Reject two ambiguous JA labels (4 rows).
    assert all(row.source_language == "en" for row in en)
    assert all(row.source_language == "ja" for row in ja)
    assert all(row.aliases == [] for row in [*en, *ja])
    assert all("ko" in row.translations for row in [*en, *ja])
    assert not any(row.term.casefold() == "baptism" for row in en)
    assert next(row for row in en if row.term == "Holy Spirit").translations["ko"] == "성령"
    assert next(row for row in ja if row.term == "聖霊").translations["ko"] == "성령"

    repository = GlossaryRepository(str(tmp_path / "missionary.db"))
    repository.initialize()
    assert repository.seed(en)
    assert len(repository.active_for(ProductPreset.CHURCH, "ko")) == 0
    assert len(repository.active_for(ProductPreset.CHURCH, "en")) == len(en)
    assert len(repository.active_for(ProductPreset.CHURCH, "ja")) == 0
    initial = next(row for row in repository.list() if row.source_language == "en")
    edited = initial.model_copy(update={"translations": {"ko": "운영자가 수정한 뜻"}})
    repository.update(initial.id, edited)
    repository.seed(en)
    assert repository.get(initial.id).translations["ko"] == "운영자가 수정한 뜻"
    repository.seed(ja)
    assert len(repository.active_for(ProductPreset.CHURCH, "ja")) == len(ja)
    assert len(repository.active_for(ProductPreset.CHURCH, "ko")) == 0


def test_foreign_church_seed_refuses_unsupported_language() -> None:
    import pytest

    from langtextflow.church_glossary_seed import church_glossary_for_source

    with pytest.raises(ValueError, match="ko/en/ja"):
        church_glossary_for_source("fr")
