from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
    source_language: str = "ko"
    target_languages: list[str] = Field(default_factory=lambda: ["en"])
    engine: str = "mock"


class SessionState(BaseModel):
    running: bool = False
    source_language: str = "ko"
    target_languages: list[str] = Field(default_factory=lambda: ["en"])
    engine: str = "mock"
    started_at: datetime | None = None
