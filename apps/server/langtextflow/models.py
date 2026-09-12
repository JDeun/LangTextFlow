from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from .context_documents import MAX_DOCUMENT_BYTES, extract_context_document


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


class CaptionFontFamily(StrEnum):
    SYSTEM = "system"
    SANS = "sans"
    SERIF = "serif"
    MONO = "mono"


class CaptionTextAlign(StrEnum):
    LEFT = "left"
    CENTER = "center"


class CaptionDisplaySettings(BaseModel):
    font_family: CaptionFontFamily = CaptionFontFamily.SYSTEM
    font_scale_percent: int = Field(default=100, ge=70, le=180)
    max_lines: int = Field(default=2, ge=1, le=4)
    hold_seconds: float = Field(default=8.0, ge=0.0, le=30.0)
    show_source_when_translated: bool = True
    text_align: CaptionTextAlign = CaptionTextAlign.CENTER


class GlossaryEntry(BaseModel):
    term: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    translations: dict[str, str] = Field(default_factory=dict)
    category: str = Field(default="general", max_length=80)
    presets: list[ProductPreset] = Field(default_factory=list)
    boost: float = Field(default=1.0, ge=0.0, le=20.0)
    enabled: bool = True

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("presets")
    @classmethod
    def normalize_presets(cls, values: list[ProductPreset]) -> list[ProductPreset]:
        return list(dict.fromkeys(values))

    def applies_to(self, preset: ProductPreset) -> bool:
        return not self.presets or preset in self.presets


class GlossaryRecord(GlossaryEntry):
    id: str
    created_at: datetime
    updated_at: datetime


class ReferenceDocument(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", max_length=120)
    size_bytes: int = Field(default=0, ge=0, le=MAX_DOCUMENT_BYTES)
    content_base64: str | None = Field(default=None, max_length=7_100_000, exclude=True, repr=False)
    text: str = Field(default="", max_length=60_000)
    character_count: int = Field(default=0, ge=0)
    truncated: bool = False
    sha256: str = Field(default="", max_length=64)

    @field_validator("filename")
    @classmethod
    def normalize_filename(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reference document filename is required")
        return normalized

    @model_validator(mode="after")
    def extract_uploaded_content(self) -> ReferenceDocument:
        if self.content_base64:
            extracted = extract_context_document(
                filename=self.filename,
                content_base64=self.content_base64,
            )
            self.size_bytes = extracted.size_bytes
            self.text = extracted.text
            self.character_count = extracted.character_count
            self.truncated = extracted.truncated
            self.sha256 = extracted.sha256
            self.content_base64 = None
        elif not self.text.strip():
            raise ValueError("reference document requires uploaded content or extracted text")
        elif self.character_count == 0:
            self.character_count = len(self.text)
        return self


class SessionContext(BaseModel):
    title: str = Field(default="Untitled session", min_length=1, max_length=120)
    presenter: str | None = Field(default=None, max_length=120)
    preset: ProductPreset = ProductPreset.GENERAL
    description: str = Field(default="", max_length=4000)
    hotwords: list[str] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    reference_documents: list[ReferenceDocument] = Field(default_factory=list, max_length=4)
    reference_text: str = Field(default="", max_length=120_000)
    output_modes: list[OutputMode] = Field(
        default_factory=lambda: [OutputMode.AUDIENCE, OutputMode.PROJECTOR, OutputMode.OBS]
    )
    audience_access: bool = True
    display_settings: CaptionDisplaySettings = Field(default_factory=CaptionDisplaySettings)

    @field_validator("hotwords")
    @classmethod
    def normalize_hotwords(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def compose_reference_text(self) -> SessionContext:
        if self.reference_documents:
            blocks = [f"[{item.filename}]\n{item.text}" for item in self.reference_documents]
            self.reference_text = "\n\n".join(blocks)[:120_000].rstrip()
        return self

    def asr_hotwords(self) -> list[str]:
        terms = list(self.hotwords)
        for entry in self.glossary:
            if not entry.enabled:
                continue
            terms.append(entry.term)
            terms.extend(entry.aliases)
        return list(dict.fromkeys(term for term in terms if term))

    def reference_excerpt(self, max_chars: int = 4000) -> str:
        if max_chars <= 0:
            return ""
        return self.reference_text[:max_chars].rstrip()


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


class TranslationStatus(BaseModel):
    enabled: bool = False
    provider: str = "none"
    model: str | None = None
    available: bool = False
    error: str | None = None


class StartSessionRequest(BaseModel):
    source_language: str = Field(default="ko", min_length=2, max_length=16)
    target_languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    engine: str = "mock"
    translation_provider: str = "none"
    translation_model: str | None = None
    context: SessionContext = Field(default_factory=SessionContext)

    @field_validator("target_languages")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("at least one target language is required")
        return normalized

    @model_validator(mode="after")
    def validate_language_flow(self) -> StartSessionRequest:
        if self.source_language in self.target_languages:
            raise ValueError("source language is always the original track and cannot be a target")
        return self


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
    translation_status: TranslationStatus = Field(default_factory=TranslationStatus)
    persistence_error: str | None = None
    started_at: datetime | None = None


class SessionRecord(BaseModel):
    session_id: str
    join_code: str
    title: str
    presenter: str | None
    preset: ProductPreset
    source_language: str
    target_languages: list[str]
    engine: str
    translation_provider: str
    translation_model: str | None
    started_at: datetime
    ended_at: datetime | None = None
    segment_count: int = 0


class SessionDetail(SessionRecord):
    context: SessionContext


class AudienceSessionView(BaseModel):
    session_id: str
    join_code: str
    running: bool
    title: str
    presenter: str | None
    preset: ProductPreset
    source_language: str
    target_languages: list[str]
    display_settings: CaptionDisplaySettings = Field(default_factory=CaptionDisplaySettings)
    started_at: datetime | None


class AudioStreamInfo(BaseModel):
    engine: str
    required: bool
    sample_rate: int
    channels: int = 1
    sample_format: str = "f32le"


class NetworkInfo(BaseModel):
    addresses: list[str] = Field(default_factory=list)
    frontend_port: int = Field(default=5173, ge=1, le=65535)
    backend_port: int = Field(default=8000, ge=1, le=65535)
