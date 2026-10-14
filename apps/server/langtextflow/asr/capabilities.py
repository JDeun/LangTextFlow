"""Truthful ASR capability descriptors for the existing runtime adapters.

These are contract declarations, not observed WER/latency certifications.
They must be revised alongside an ASR adapter if its behavior changes.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

AsrClock = Literal["chunk_segment", "sequence_estimate", "synthetic", "dynamic"]
HotwordMode = Literal["native_hint", "prompt_hint", "unsupported", "dynamic"]


class AsrCapability(BaseModel):
    provider: str
    label: str
    implementation: Literal["chunked", "streamed_sidecar", "synthetic", "fallback"]
    emitted_stages: list[Literal["partial", "stable"]]
    timestamp_origin: AsrClock
    hotwords: HotwordMode
    word_timestamps: bool
    declared_languages: list[str]
    model_language_quality_verified: bool = False
    native_session_cancellation_verified: bool = False
    warnings: list[str]


def asr_capabilities() -> list[AsrCapability]:
    # Advertise only the stage/timestamp behavior present in source.
    # In particular, every real ASR adapter currently emits STABLE only;
    # COMMITTED is produced downstream by CaptionPipeline.
    return [
        AsrCapability(
            provider="faster-whisper",
            label="Faster-Whisper chunked CPU/GPU ASR",
            implementation="chunked",
            emitted_stages=["stable"],
            timestamp_origin="chunk_segment",
            hotwords="native_hint",
            word_timestamps=False,
            declared_languages=["ko", "en", "ja"],
            warnings=[
                "Chunked inference (not token-by-token streaming).",
                "Model availability and language-specific WER must be verified.",
                "Hotwords are hints, not guaranteed recognition.",
            ],
        ),
        AsrCapability(
            provider="vibevoice",
            label="VibeVoice configured sidecar",
            implementation="streamed_sidecar",
            emitted_stages=["stable"],
            timestamp_origin="sequence_estimate",
            hotwords="prompt_hint",
            word_timestamps=False,
            declared_languages=["ko", "en", "ja"],
            warnings=[
                "Timestamps are estimated from output sequence/chunk duration.",
                "Prompt-provided vocabulary is not native ASR hotword bias.",
                "Configured sidecar may consume GPU/remote resources.",
            ],
        ),
        AsrCapability(
            provider="auto",
            label="ASR fallback (resource-profile dependent)",
            implementation="fallback",
            emitted_stages=["stable"],
            timestamp_origin="dynamic",
            hotwords="dynamic",
            word_timestamps=False,
            declared_languages=["ko", "en", "ja"],
            warnings=[
                "Selected adapter and timestamp origin can change after failover.",
                "Low-resource mode chooses Faster-Whisper only.",
                "Do not report per-language field quality from provider metadata.",
            ],
        ),
        AsrCapability(
            provider="mock",
            label="Synthetic captions only (not speech recognition)",
            implementation="synthetic",
            emitted_stages=["partial", "stable"],
            timestamp_origin="synthetic",
            hotwords="unsupported",
            word_timestamps=False,
            declared_languages=["ko", "en", "ja"],
            warnings=["Mock emits canned text regardless of input language/audio."],
        ),
    ]
