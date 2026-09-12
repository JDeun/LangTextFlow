from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from .context_documents import MAX_DOCUMENT_BYTES, extract_context_document

MAX_HOTWORDS = 256
MAX_HOTWORD_CHARS = 160
MAX_GLOSSARY_ENTRIES = 500
MAX_GLOSSARY_ALIASES = 64
MAX_GLOSSARY_TRANSLATIONS = 32
MAX_TRANSLATION_TEXT_CHARS = 20_000
MAX_TRANSCRIPT_TEXT_CHARS = 20_000
MAX_TARGET_LANGUAGES = 10


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
    aliases: list[str] = Field(default_factory=list, max_length=MAX_GLOSSARY_ALIASES)
    translations: dict[str, str] = Field(
        default_factory=dict,
        max_length=MAX_GLOSSARY_TRANSLATIONS,
    )
    category: str = Field(default="general", max_length=80)
    presets: list[ProductPreset] = Field(default_factory=list, max_length=4)
    boost: float = Field(default=1.0, ge=0.0, le=20.0)
    enabled: bool = True

    @field_validator("term", "category")
    @classmethod
    def normalize_short_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("glossary text cannot be empty")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                continue
            if len(item) > 160:
                raise ValueError("glossary alias exceeds 160 characters")
            normalized.append(item)
        return list(dict.fromkeys(normalized))

    @field_validator("translations")
    @classmethod
    def normalize_translations(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_language, raw_text in values.items():
            language = raw_language.strip().lower()
            text = raw_text.strip()
            if not 2 <= len(language) <= 16:
                raise ValueError("translation language key must be 2-16 characters")
            if not text:
                continue
            if len(text) > 2000:
                raise ValueError("glossary translation exceeds 2000 characters")
            normalized[language] = text
        return normalized

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
    hotwords: list[str] = Field(default_factory=list, max_length=MAX_HOTWORDS)
    glossary: list[GlossaryEntry] = Field(default_factory=list, max_length=MAX_GLOSSARY_ENTRIES)
    reference_documents: list[ReferenceDocument] = Field(default_factory=list, max_length=4)
    reference_text: str = Field(default="", max_length=120_000)
    output_modes: list[OutputMode] = Field(
        default_factory=lambda: [OutputMode.AUDIENCE, OutputMode.PROJECTOR, OutputMode.OBS],
        max_length=5,
    )
    audience_access: bool = True
    display_settings: CaptionDisplaySettings = Field(default_factory=CaptionDisplaySettings)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session title is required")
        return normalized

    @field_validator("presenter")
    @classmethod
    def normalize_presenter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("hotwords")
    @classmethod
    def normalize_hotwords(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item:
                continue
            if len(item) > MAX_HOTWORD_CHARS:
                raise ValueError(f"hotword exceeds {MAX_HOTWORD_CHARS} characters")
            normalized.append(item)
        return list(dict.fromkeys(normalized))

    @field_validator("output_modes")
    @classmethod
    def normalize_output_modes(cls, values: list[OutputMode]) -> list[OutputMode]:
        return list(dict.fromkeys(values))

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
        return list(dict.fromkeys(term for term in terms if term))[:1000]

    def reference_excerpt(self, max_chars: int = 4000) -> str:
        if max_chars <= 0:
            return ""
        return self.reference_text[:max_chars].rstrip()


class CorrectionProvenance(BaseModel):
    method: str = Field(pattern="^(deterministic|llm|fallback)$")
    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    deterministic_changed: bool = False
    llm_attempted: bool = False
    llm_applied: bool = False
    changed: bool = False
    fallback_reason: str | None = Field(default=None, max_length=1000)


class TranscriptEvent(BaseModel):
    type: str = Field(default="transcript", max_length=32)
    segment_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    stage: CaptionStage
    source_language: str = Field(min_length=2, max_length=16)
    text: str = Field(max_length=MAX_TRANSCRIPT_TEXT_CHARS)
    translations: dict[str, str] = Field(default_factory=dict, max_length=MAX_TARGET_LANGUAGES)
    start_ms: int = Field(ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    speaker: str | None = Field(default=None, max_length=160)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    correction: CorrectionProvenance | None = None
    committed: bool = False
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("source_language")
    @classmethod
    def normalize_source_language(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("translations")
    @classmethod
    def validate_translations(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_language, raw_text in values.items():
            language = raw_language.strip().lower()
            text = raw_text.strip()
            if not 2 <= len(language) <= 16:
                raise ValueError("translation language key must be 2-16 characters")
            if len(text) > MAX_TRANSLATION_TEXT_CHARS:
                raise ValueError("translation text exceeds the safety length limit")
            normalized[language] = text
        return normalized

    @model_validator(mode="after")
    def validate_commit_state(self) -> TranscriptEvent:
        if self.committed and self.stage is not CaptionStage.COMMITTED:
            raise ValueError("committed=true requires stage=committed")
        if self.end_ms is not None and self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class ProviderStatus(BaseModel):
    enabled: bool = False
    provider: str = Field(default="none", max_length=80)
    model: str | None = Field(default=None, max_length=200)
    available: bool = False
    error: str | None = Field(default=None, max_length=2000)


class CorrectionStatus(ProviderStatus):
    pass


class TranslationStatus(ProviderStatus):
    pass


class StartSessionRequest(BaseModel):
    source_language: str = Field(default="ko", min_length=2, max_length=16)
    target_languages: list[str] = Field(
        default_factory=lambda: ["en"],
        min_length=1,
        max_length=MAX_TARGET_LANGUAGES,
    )
    engine: str = Field(default="mock", max_length=40)
    correction_provider: str = Field(default="none", max_length=80)
    correction_model: str | None = Field(default=None, max_length=200)
    translation_provider: str = Field(default="none", max_length=80)
    translation_model: str | None = Field(default=None, max_length=200)
    context: SessionContext = Field(default_factory=SessionContext)

    @field_validator("source_language")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("target_languages")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        normalized = list(
            dict.fromkeys(value.strip().lower() for value in values if value.strip())
        )
        if not normalized:
            raise ValueError("at least one target language is required")
        if any(not 2 <= len(value) <= 16 for value in normalized):
            raise ValueError("target language must be 2-16 characters")
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
    correction_status: CorrectionStatus = Field(default_factory=CorrectionStatus)
    translation_status: TranslationStatus = Field(default_factory=TranslationStatus)
    persistence_error: str | None = None
    started_at: datetime | None = None


class SessionRecord(BaseModel):
    session_id: str
    join_code: str
    title: str
    notes: str = ""
    presenter: str | None
    preset: ProductPreset
    source_language: str
    target_languages: list[str]
    engine: str
    correction_provider: str = "none"
    correction_model: str | None = None
    translation_provider: str
    translation_model: str | None
    started_at: datetime
    ended_at: datetime | None = None
    segment_count: int = 0


class SessionDetail(SessionRecord):
    context: SessionContext


class SessionMetadataUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=8000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session title is required")
        return normalized

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str) -> str:
        return value.strip()


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
