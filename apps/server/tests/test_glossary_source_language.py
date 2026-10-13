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
    assert en.context.asr_hotwords() == ["grace", "grace alias"]
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
