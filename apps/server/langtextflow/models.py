from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class CaptionStage(StrEnum):
    PARTIAL = "partial"
    STABLE = "stable"
    CORRECTED = "corrected"
    TRANSLATED = "translated"
    COMMITTED = "committed"


STAGE_ORDER: dict[CaptionStage, int] = {
    CaptionStage.PARTIAL: 0,
    CaptionStage.STABLE: 1,
    CaptionStage.CORRECTED: 2,
    CaptionStage.TRANSLATED: 3,
    CaptionStage.COMMITTED: 4,
}


class ProductPreset(StrEnum):
    GENERAL = "general"
    CHURCH = "church"
    CONFERENCE = "conference"
    LECTURE = "lecture"


class OutputMode(StrEnum):
    OPERATOR = "operator"
    AUDIENCE = "audience"
    PROJECTOR = "projector"
    OBS = "obs"
    OVERLAY = "overlay"


class GlossaryEntry(BaseModel):
    term: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    translations: dict[str, str] = Field(default_factory=dict)
    category: str = Field(default="general", max_length=80)
    boost: float = Field(default=1.0, ge=0.0, le=20.0)
    enabled: bool = True

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class SessionContext(BaseModel):
    title: str = Field(default="Untitled session", min_length=1, max_length=120)
    presenter: str | None = Field(default=None, max_length=120)
    preset: ProductPreset = ProductPreset.GENERAL
    description: str = Field(default="", max_length=4000)
    hotwords: list[str] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    output_modes: list[OutputMode] = Field(
        default_factory=lambda: [OutputMode.AUDIENCE, OutputMode.PROJECTOR, OutputMode.OBS]
    )
    audience_access: bool = True

    @field_validator("hotwords")
    @classmethod
    def normalize_hotwords(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    def asr_hotwords(self) -> list[str]:
        terms = list(self.hotwords)
        for entry in self.glossary:
            if not entry.enabled:
                continue
            terms.append(entry.term)
            terms.extend(entry.aliases)
        return list(dict.fromkeys(term for term in terms if term))


class TranscriptEvent(BaseModel):
    type: str = "transcript"
    segment_id: str
    version: int = Field(ge=1)
    stage: CaptionStage
    source_language: str = Field(min_length=2, max_length=16)
    text: str
    translations: dict[str, str] = Field(default_factory=dict)
    start_ms: int = Field(ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    speaker: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    committed: bool = False
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_commit_state(self) -> TranscriptEvent:
        if self.committed and self.stage is not CaptionStage.COMMITTED:
            raise ValueError("committed=true requires stage=committed")
        if self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class StartSessionRequest(BaseModel):
    source_language: str = Field(default="ko", min_length=2, max_length=16)
    target_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    engine: str = "mock"
    context: SessionContext = Field(default_factory=SessionContext)

    @field_validator("target_languages")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("at least one target language is required")
        return normalized


class SessionState(BaseModel):
    session_id: str | None = None
    join_code: str | None = None
    running: bool = False
    source_language: str = "ko"
    target_languages: list[str] = Field(default_factory=lambda: ["en"])
    engine: str = "mock"
    context: SessionContext | None = None
    audio_required: bool = False
    audio_sample_rate: int | None = None
    started_at: datetime | None = None


class AudienceSessionView(BaseModel):
    session_id: str
    join_code: str
    running: bool
    title: str
    presenter: str | None
    preset: ProductPreset
    source_language: str
    target_languages: list[str]
    started_at: datetime | None


class AudioStreamInfo(BaseModel):
    engine: str
    required: bool
    sample_rate: int
    channels: int = 1
    sample_format: str = "f32le"
