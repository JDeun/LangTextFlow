from __future__ import annotations

import json
from datetime import datetime

from .models import SessionDetail, TranscriptEvent


def _timestamp(milliseconds: int, separator: str) -> str:
    total_seconds, millis = divmod(max(milliseconds, 0), 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


# Text exports must enforce the same language contract as the live surfaces.
# A missing target translation must never be replaced by the source transcript.
_MISSING_TRANSLATIONS = {
    "ko": ("한국어 번역 중…", "한국어 번역을 사용할 수 없습니다."),
    "en": ("Translating into English…", "English translation unavailable."),
    "ja": ("日本語に翻訳中…", "日本語訳を利用できません。"),
}
_UNKNOWN_TRANSLATION = (
    "Translation pending.", "Translation unavailable."
)


def _caption_text(event: TranscriptEvent, language: str | None) -> str:
    if language is None or language == event.source_language:
        return event.text
    translated = event.translations.get(language, "").strip()
    if translated:
        return translated
    pending, unavailable = _MISSING_TRANSLATIONS.get(
        language, _UNKNOWN_TRANSLATION
    )
    # A committed segment has no successful target translation. Do not
    # label the failure as a specific provider error without provenance.
    return unavailable if event.committed else pending


def _subtitle_text(event: TranscriptEvent, language: str | None) -> str:
    # Blank lines delimit cues in SRT/WebVTT. Normalize provider/model output so
    # transcript text cannot accidentally terminate one cue and inject another.
    value = _caption_text(event, language).replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in value.split("\n") if line.strip()]
    return "\n".join(lines)


def _end_ms(event: TranscriptEvent) -> int:
    return event.end_ms if event.end_ms is not None else event.start_ms + 3000


def export_srt(segments: list[TranscriptEvent], language: str | None = None) -> str:
    blocks: list[str] = []
    for index, event in enumerate(segments, start=1):
        start = _timestamp(event.start_ms, ",")
        end = _timestamp(_end_ms(event), ",")
        blocks.append(f"{index}\n{start} --> {end}\n{_subtitle_text(event, language)}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_vtt(segments: list[TranscriptEvent], language: str | None = None) -> str:
    blocks = ["WEBVTT"]
    for event in segments:
        start = _timestamp(event.start_ms, ".")
        end = _timestamp(_end_ms(event), ".")
        blocks.append(f"{start} --> {end}\n{_subtitle_text(event, language)}")
    return "\n\n".join(blocks) + "\n"


def export_txt(segments: list[TranscriptEvent], language: str | None = None) -> str:
    lines = [_caption_text(event, language) for event in segments]
    return "\n".join(lines) + ("\n" if lines else "")


def export_json(
    session: SessionDetail,
    segments: list[TranscriptEvent],
) -> str:
    payload = {
        "session": session.model_dump(mode="json"),
        "segments": [event.model_dump(mode="json") for event in segments],
        "exported_at": datetime.now().astimezone().isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
