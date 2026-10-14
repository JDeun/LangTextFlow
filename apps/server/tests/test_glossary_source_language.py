"""Source-language glossary migration and foreign missionary session isolation."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.glossary_transfer import (
    GlossaryImportRequest,
    GlossaryTransferFormat,
    export_glossary,
    parse_glossary_import,
)
from langtextflow.models import (
    GlossaryEntry,
    ProductPreset,
    SessionContext,
    StartSessionRequest,
)
from langtextflow.translation.ollama import OllamaTranslator


def test_legacy_sqlite_migration_retains_korean_terms_and_record_metadata(tmp_path) -> None:
    db = tmp_path / "old.db"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE glossary_entries ("
            "id TEXT PRIMARY KEY, term TEXT NOT NULL, aliases_json TEXT NOT NULL, "
            "translations_json TEXT NOT NULL, category TEXT NOT NULL, "
            "presets_json TEXT NOT NULL DEFAULT '[]', boost REAL NOT NULL, "
            "enabled INTEGER NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO glossary_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy-id", "구원", '["구원"]', '{"en":"salvation"}',
             "theology", '["church"]', 1.5, 1, now, now),
        )
    repo = GlossaryRepository(str(db))
    repo.initialize()
    repo.initialize()  # Migration must be repeatable.
    saved = repo.get("legacy-id")
    assert saved is not None
    assert saved.id == "legacy-id"
    assert saved.source_language == "ko"
    assert saved.translations == {"en": "salvation"}
    assert repo.active_for(ProductPreset.CHURCH, "en") == []
    assert len(repo.active_for(ProductPreset.CHURCH, "ko")) == 1


def test_same_canonical_term_can_exist_in_different_source_languages(tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "new.db"))
    repo.initialize()
    en = repo.create(GlossaryEntry(
        term="grace", source_language="EN",
        translations={"ko": "은혜", "ja": "恵み"},
        aliases=["the grace"], presets=[ProductPreset.CHURCH],
    ))
    ja = repo.create(GlossaryEntry(
        term="grace", source_language="ja",
        translations={"ko": "恩寵"}, presets=[ProductPreset.CHURCH],
    ))
    assert en.source_language == "en"
    assert len(repo.active_for(ProductPreset.CHURCH, "en")) == 1
    assert len(repo.active_for(ProductPreset.CHURCH, "ja")) == 1
    assert repo.active_for(ProductPreset.CHURCH, "ko") == []
    repo.seed([
        GlossaryEntry(term="grace", source_language="en",
                      translations={"ko": "변경 금지"}),
        GlossaryEntry(term="grace", source_language="ko",
                      translations={"en": "grace"}),
    ])
    assert len(repo.list()) == 3
    assert repo.get(en.id).translations["ko"] == "은혜"
    assert repo.get(ja.id).translations["ko"] == "恩寵"


def test_same_language_duplicate_is_rejected_but_other_language_is_valid(tmp_path) -> None:
    import pytest

    repo = GlossaryRepository(str(tmp_path / "conflicts.db"))
    repo.initialize()
    english = repo.create(GlossaryEntry(
        term="Grace", source_language="en", translations={"ko": "은혜"},
    ))
    with pytest.raises(ValueError, match="duplicate glossary term"):
        repo.create(GlossaryEntry(
            term="grace", source_language="en", translations={"ko": "호의"},
        ))
    korean = repo.create(GlossaryEntry(
        term="grace", source_language="ko", translations={"en": "grace"},
    ))
    with pytest.raises(ValueError, match="duplicate glossary term"):
        repo.update(
            korean.id,
            GlossaryEntry(
                term="Grace", source_language="en", translations={"ko": "은혜"},
            ),
        )
    assert repo.get(english.id).source_language == "en"
    assert repo.get(korean.id).source_language == "ko"


def test_session_snapshot_selects_only_matching_english_and_japanese_terms(
    monkeypatch, tmp_path,
) -> None:
    repo = GlossaryRepository(str(tmp_path / "session.db"))
    repo.initialize()
    for language, term, translations in [
        ("ko", "은혜", {"en": "grace", "ja": "恵み"}),
        ("en", "grace", {"ko": "은혜", "ja": "恵み"}),
        ("ja", "恵み", {"ko": "은혜", "en": "grace"}),
    ]:
        repo.create(GlossaryEntry(
            term=term, source_language=language, translations=translations,
            aliases=[f"{term} alias"], presets=[ProductPreset.CHURCH],
        ))
    monkeypatch.setattr(main, "glossary_repository", repo)
    en = main._with_saved_glossary(StartSessionRequest(
        source_language="en", target_languages=["ko"],
        context=SessionContext(preset=ProductPreset.CHURCH),
    ))
    assert [entry.term for entry in en.context.glossary] == ["grace"]
    assert en.context.asr_hotwords(source_language="en") == ["grace", "grace alias"]
    prompt = OllamaTranslator.build_prompt(
        "The grace of God", source_language="en", target_language="ko",
        context=en.context,
    )
    assert "grace -> 은혜" in prompt
    assert "恵み -> 은혜" not in prompt
    jp = main._with_saved_glossary(StartSessionRequest(
        source_language="ja", target_languages=["ko"],
        context=SessionContext(preset=ProductPreset.CHURCH),
    ))
    assert [entry.term for entry in jp.context.glossary] == ["恵み"]


def test_json_csv_round_trip_source_language_and_legacy_import(tmp_path) -> None:
    repo = GlossaryRepository(str(tmp_path / "roundtrip.db"))
    repo.initialize()
    repo.create(GlossaryEntry(
        source_language="ja", term="恵み", aliases=["めぐみ"],
        translations={"ko": "은혜", "en": "grace"},
    ))
    for fmt in [GlossaryTransferFormat.JSON, GlossaryTransferFormat.CSV]:
        serialized = export_glossary(repo.list(), fmt)
        parsed = parse_glossary_import(
            GlossaryImportRequest(format=fmt, content=serialized)
        )
        assert parsed[0].source_language == "ja"
        assert parsed[0].translations["ko"] == "은혜"

    old = GlossaryImportRequest(format=GlossaryTransferFormat.JSON, content=json.dumps({
        "schema_version": 1,
        "entries": [{"term": "믿음", "translations": {"en": "faith"}}],
    }))
    assert parse_glossary_import(old)[0].source_language == "ko"
    count = repo.import_entries(
        [GlossaryEntry(source_language="en", term="恵み",
                       translations={"ko": "자비"})], conflict_policy="skip",
    )
    assert count == (1, 0, 0)
    assert len(repo.list()) == 2



def test_bulk_korean_church_seed_never_becomes_foreign_source_hotwords(tmp_path) -> None:
    """157 KO canonical terms must not bias EN/JA recognition by accident."""
    from langtextflow.church_glossary_seed import CHURCH_GLOSSARY

    repo = GlossaryRepository(str(tmp_path / "bulk-source-scope.db"))
    repo.initialize()
    seeded = repo.seed(CHURCH_GLOSSARY)
    assert len(seeded) == 157
    assert len(repo.active_for(ProductPreset.CHURCH, "ko")) == 157
    assert repo.active_for(ProductPreset.CHURCH, "en") == []
    assert repo.active_for(ProductPreset.CHURCH, "ja") == []

    # Foreign canonical terms are explicit independent operator decisions.
    english = repo.create(GlossaryEntry(
        source_language="en", term="Holy Spirit",
        aliases=["Spirit of God"], translations={"ko": "성령"},
        presets=[ProductPreset.CHURCH],
    ))
    japanese = repo.create(GlossaryEntry(
        source_language="ja", term="聖霊",
        aliases=["せいれい"], translations={"ko": "성령"},
        presets=[ProductPreset.CHURCH],
    ))
    assert [item.term for item in repo.active_for(ProductPreset.CHURCH, "en")] == [
        english.term
    ]
    assert [item.term for item in repo.active_for(ProductPreset.CHURCH, "ja")] == [
        japanese.term
    ]
    assert len(repo.active_for(ProductPreset.CHURCH, "ko")) == 157


def test_asr_hotword_source_isolation_even_for_mixed_injected_session_context() -> None:
    """Guard the ASR adapter boundary, not only the saved-glossary loader."""
    context = SessionContext(
        preset=ProductPreset.CHURCH,
        hotwords=["Operator explicitly selected"],
        glossary=[
            GlossaryEntry(
                source_language="ko", term="성령", aliases=["거룩한 영"],
                translations={"en": "Holy Spirit"},
                presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                source_language="en", term="Holy Spirit",
                aliases=["Spirit of God"], translations={"ko": "성령"},
                presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                source_language="ja", term="聖霊", aliases=["せいれい"],
                translations={"ko": "성령"}, presets=[ProductPreset.CHURCH],
            ),
            GlossaryEntry(
                source_language="en", term="Disabled hint", enabled=False,
            ),
            GlossaryEntry(
                source_language="en", term="Conference only",
                presets=[ProductPreset.CONFERENCE],
            ),
        ],
    )
    assert context.asr_hotwords(source_language="ko") == [
        "Operator explicitly selected", "성령", "거룩한 영",
    ]
    assert context.asr_hotwords(source_language="en") == [
        "Operator explicitly selected", "Holy Spirit", "Spirit of God",
    ]
    assert context.asr_hotwords(source_language="ja") == [
        "Operator explicitly selected", "聖霊", "せいれい",
    ]
    # Unverified or unknown source cannot silently bias using all languages.
    assert context.asr_hotwords() == ["Operator explicitly selected"]
    assert context.asr_hotwords(source_language="xx") == [
        "Operator explicitly selected",
    ]


def test_asr_adapters_pass_verified_source_language_to_glossary() -> None:
    """Prevent accidental regression to unfiltered context.asr_hotwords()."""
    from inspect import getsource

    from langtextflow.asr.faster_whisper import FasterWhisperStreamingAsrEngine
    from langtextflow.asr.vibevoice import VibeVoiceStreamingAsrEngine

    assert "context.asr_hotwords(source_language=request.source_language)" in (
        getsource(FasterWhisperStreamingAsrEngine)
    )
    assert "context.asr_hotwords(source_language=request.source_language)" in (
        getsource(VibeVoiceStreamingAsrEngine)
    )
