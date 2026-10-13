from datetime import UTC, datetime

from langtextflow.exports import export_json, export_srt, export_txt, export_vtt
from langtextflow.models import (
    CaptionStage,
    ProductPreset,
    SessionContext,
    SessionDetail,
    TranscriptEvent,
)


def _segment() -> TranscriptEvent:
    return TranscriptEvent(
        segment_id="seg-1",
        version=4,
        stage=CaptionStage.COMMITTED,
        source_language="ko",
        text="요한복음 3장입니다.",
        translations={"en": "This is John chapter 3."},
        start_ms=1234,
        end_ms=5678,
        committed=True,
    )


def _session() -> SessionDetail:
    return SessionDetail(
        session_id="session-1",
        join_code="ABC123",
        title="Mission Meeting",
        presenter="John Smith",
        preset=ProductPreset.CHURCH,
        source_language="ko",
        target_languages=["en"],
        engine="vibevoice",
        translation_provider="ollama",
        translation_model="translategemma:4b",
        started_at=datetime(2026, 9, 12, 0, 0, tzinfo=UTC),
        segment_count=1,
        context=SessionContext(title="Mission Meeting", preset=ProductPreset.CHURCH),
    )


def test_srt_and_vtt_use_requested_translation() -> None:
    segment = _segment()
    srt = export_srt([segment], "en")
    vtt = export_vtt([segment], "en")

    assert "00:00:01,234 --> 00:00:05,678" in srt
    assert "This is John chapter 3." in srt
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.234 --> 00:00:05.678" in vtt


def test_srt_and_vtt_cannot_be_split_by_caption_blank_lines() -> None:
    segment = _segment().model_copy(
        update={
            "text": "first line\r\n\r\n99\n00:00:00,000 --> 99:00:00,000\nsecond line",
            "translations": {},
        }
    )

    srt = export_srt([segment])
    vtt = export_vtt([segment])

    assert "first line\n99\n00:00:00,000 --> 99:00:00,000\nsecond line" in srt
    assert "first line\n99\n00:00:00,000 --> 99:00:00,000\nsecond line" in vtt
    assert "first line\n\n99" not in srt
    assert "first line\n\n99" not in vtt


def test_missing_target_translation_never_exports_source_as_translation() -> None:
    committed = _segment()
    for formatter in (export_txt, export_srt, export_vtt):
        output = formatter([committed], "ja")
        assert "日本語訳を利用できません。" in output
        assert "요한복음 3장입니다." not in output


def test_pending_target_translation_exports_explicit_pending_status() -> None:
    pending = _segment().model_copy(
        update={"committed": False, "stage": CaptionStage.PARTIAL, "translations": {}}
    )
    assert export_txt([pending], "en") == "Translating into English…\n"
    assert "日本語に翻訳中…" in export_vtt([pending], "ja")
    assert "한국어 번역 중…" in export_srt(
        [pending.model_copy(update={"source_language": "ja"})], "ko"
    )


def test_source_is_exported_only_when_explicitly_requested() -> None:
    event = _segment()
    assert export_txt([event]) == "요한복음 3장입니다.\n"
    assert export_txt([event], "ko") == "요한복음 3장입니다.\n"
    assert "This is John chapter 3." in export_srt([event], "en")


def test_mixed_translation_availability_keeps_cue_order_and_language() -> None:
    first = _segment()
    missing = first.model_copy(update={
        "segment_id": "seg-2", "start_ms": 6000, "end_ms": 8000,
        "translations": {},
    })
    restored = first.model_copy(update={
        "segment_id": "seg-3", "start_ms": 9000, "end_ms": 11000,
        "translations": {"en": "The next English translation."},
    })
    srt = export_srt([first, missing, restored], "en")
    assert srt.startswith("1\n00:00:01,234")
    assert "\n2\n00:00:06,000 --> 00:00:08,000\nEnglish translation unavailable." in srt
    assert "\n3\n00:00:09,000 --> 00:00:11,000\nThe next English translation." in srt
    assert "요한복음 3장입니다." not in srt


def test_unknown_target_code_uses_safe_neutral_message_without_echoing_input() -> None:
    event = _segment()
    untrusted = "en\\n00:00:00 --> evil"
    assert export_txt([event], untrusted) == "Translation unavailable.\n"


def test_json_export_contains_session_and_segments() -> None:
    payload = export_json(_session(), [_segment()])
    assert '"session_id": "session-1"' in payload
    assert '"segment_id": "seg-1"' in payload
    assert '"Gospel' not in payload
