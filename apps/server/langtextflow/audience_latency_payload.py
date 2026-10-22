"""Strict opt-in audience latency payload validation, with no ingestion route.

Reject unknown fields so clients cannot accidentally transmit transcript text,
identifiers, or timestamps to the telemetry service.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .audience_latency import MAX_LATENCY_MS


class AudienceLatencyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    stage: Literal["partial", "stable", "translated", "committed"]
    receive_to_display_ms: float
    timing_method: Literal["double_raf_estimate"]

    @field_validator("receive_to_display_ms")
    @classmethod
    def validate_latency(cls, value: float) -> float:
        if not math.isfinite(value) or not 0 <= value <= MAX_LATENCY_MS:
            raise ValueError("receive_to_display_ms must be finite and bounded")
        return value
