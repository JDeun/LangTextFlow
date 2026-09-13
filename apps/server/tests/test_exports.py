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


def test_txt_falls_back_to_source_when_translation_missing() -> None:
    assert export_txt([_segment()], "ja") == "요한복음 3장입니다.\n"


def test_json_export_contains_session_and_segments() -> None:
    payload = export_json(_session(), [_segment()])
    assert '"session_id": "session-1"' in payload
    assert '"segment_id": "seg-1"' in payload
    assert '"Gospel' not in payload
