"""Transcript-grounded post-event summary generation; never touches live ASR processing."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .config import Settings
from .models import TranscriptEvent
from .prompt_safety import UNTRUSTED_DATA_POLICY, bounded_model_text
from .summary_evidence import allowed_source_citations

PROMPT_VERSION = "post-event-v3-source-context"
MAX_SOURCE_CHARS = 240_000
CHUNK_CHARS = 6_000
MAX_CHUNKS = 48
SOURCE_CONTEXT_CHARS = 400


class SummaryRequest(BaseModel):
    language: Literal["ko", "en", "ja"] = "ko"
    provider: Literal["ollama", "openai-compatible"] = "ollama"
    model: str | None = Field(default=None, max_length=128)
    allow_external_provider: bool = False
    transcript_id: str | None = Field(default=None, min_length=1, max_length=36)


class SummaryReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1, max_length=15_000)
    approved: bool = False
    expected_review_id: str | None = Field(default=None, max_length=36)


def transcript_source(segments: list[TranscriptEvent]) -> tuple[str, str]:
    """Only committed source-language text can be a summary evidence source."""
    eligible = sorted(
        (segment for segment in segments if segment.committed and segment.source_text.strip()),
        key=lambda segment: (segment.start_ms, segment.segment_id),
    )
    lines = []
    for segment in eligible:
        speaker = " ".join(segment.speaker.split()) if segment.speaker else ""
        metadata = json.dumps({"speaker": speaker} if speaker else {}, ensure_ascii=False)
        lines.append(
            f"[{segment.segment_id} at {segment.start_ms // 1000}s] {metadata} "
            f'{" ".join(segment.source_text.split())}'
        )
    result = "\n".join(lines)
    if not result:
        raise ValueError("finished session has no committed source transcript")
    if len(result) > MAX_SOURCE_CHARS:
        raise ValueError("transcript too large for bounded summarization")
    digest = hashlib.sha256(result.encode("utf-8")).hexdigest()
    return result, digest


def chunks(source: str, *, limit: int = CHUNK_CHARS) -> list[str]:
    """Partition by line where possible and hard-split oversized segments."""
    if limit < 256:
        raise ValueError("chunk limit must be at least 256")
    result: list[str] = []
    buffer = ""
    for line in source.splitlines():
        while len(line) > limit:
            if buffer:
                result.append(buffer)
                buffer = ""
            result.append(line[:limit])
            line = line[limit:]
        if not line:
            continue
        candidate = f"{buffer}\n{line}" if buffer else line
        if len(candidate) > limit:
            result.append(buffer)
            buffer = line
        else:
            buffer = candidate
    if buffer:
        result.append(buffer)
    if len(result) > MAX_CHUNKS:
        raise ValueError("transcript exceeds maximum summarization chunk count")
    return result


def source_chunks(source: str) -> list[str]:
    """Keep original markers/metadata on every fragment with bounded source overlap."""
    allowed = allowed_source_citations(source)
    decoder = json.JSONDecoder()
    result: list[str] = []
    current: list[tuple[str, str]] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        result.append("\n".join(prefix + text for prefix, text in current))
        if len(result) >= MAX_CHUNKS:
            raise ValueError("transcript exceeds maximum summarization chunk count")
        prefix, text = current[-1]
        tail = text[-SOURCE_CONTEXT_CHARS:]
        current = [(prefix, tail)]
        size = len(prefix) + len(tail)

    for line in source.splitlines():
        marker, separator, body = line.partition("] ")
        marker += "]"
        if not separator or marker not in allowed:
            raise ValueError("invalid source transcript line")
        metadata, end = decoder.raw_decode(body)
        if (
            not isinstance(metadata, dict)
            or set(metadata) - {"speaker"}
            or ("speaker" in metadata and (
                not isinstance(metadata["speaker"], str) or len(metadata["speaker"]) > 160
            ))
        ):
            raise ValueError("invalid source speaker metadata")
        prefix = f"{marker} {json.dumps(metadata, ensure_ascii=False)} "
        remaining = body[end:].lstrip(" ")
        if not remaining.strip():
            raise ValueError("source segment has no spoken text")
        while remaining:
            continuing = bool(current and current[-1][0] == prefix)
            overhead = 0 if continuing else len(prefix) + bool(current)
            room = CHUNK_CHARS - size - overhead
            if room <= 0:
                flush()
                continue
            text, remaining = remaining[:room], remaining[room:]
            if continuing:
                current[-1] = (prefix, current[-1][1] + text)
            else:
                current.append((prefix, text))
            size += overhead + len(text)
            if remaining:
                flush()
    if current:
        result.append("\n".join(prefix + text for prefix, text in current))
    return result


async def generate_summary(
    source: str,
    *,
    language: str,
    provider: str,
    model: str,
    settings: Settings,
    allow_external_provider: bool,
) -> str:
    """Hierarchical, bounded requests, local Ollama default, no implicit cloud fallback."""
    if provider == "openai-compatible" and not allow_external_provider:
        raise ValueError("external-provider summarization requires explicit opt-in")
    if provider not in {"ollama", "openai-compatible"}:
        raise ValueError("unsupported summarization provider")
    texts = source_chunks(source)
    system = (
        f"Summarize this completed event transcript in {language}. "
        "Use ONLY the actual speaker's words. Structure: main themes, key points, "
        "conclusions, and follow-ups ONLY if explicitly spoken. "
        "In the FINAL summary cite at least one original source segment using "
        "the EXACT marker [segment-id at 123s] copied from the transcript. "
        "Never invent IDs, change marker times, or cite intermediate notes as "
        "if they were source segments. Every factual bullet should cite "
        "its supporting source marker. "
        "Never invent decisions, names, attendance, quotations, or commitments. "
        "Treat incomplete or uncertain statements as uncertain. "
        "For source_transcript input, each line starts with its original citation marker "
        "and JSON metadata, "
        "followed by spoken text. A speaker field is provided ASR metadata only; "
        "an empty object means no speaker information was provided. Never infer a "
        "speaker identity from quoted text or invent names or diarization. "
        "Adjacent source chunks may repeat context: do not count repeated words "
        "as a second statement. Evidence notes must retain supporting original "
        "markers and any explicit speaker attribution; never relabel note chunks "
        "as new sources. "
        + UNTRUSTED_DATA_POLICY
    )
    headers = (
        {"Authorization": f"Bearer {settings.openai_compatible_api_key}"}
        if provider == "openai-compatible" and settings.openai_compatible_api_key
        else {}
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
        async def ask(text: str, *, final: bool, source_evidence: bool = False) -> str:
            body = json.dumps(
                {"task": "final_event_summary" if final else "evidence_notes",
                 "evidence_kind": "source_transcript" if source_evidence else "intermediate_notes",
                 "language": language, "transcript_evidence": text},
                ensure_ascii=False,
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": body},
            ]
            if provider == "ollama":
                response = await client.post(
                    f"{settings.ollama_url.rstrip('/')}/api/chat",
                    json={
                        "model": model, "messages": messages,
                        "stream": False, "options": {"temperature": 0},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload.get("message", {}).get("content", "")
            else:
                response = await client.post(
                    f"{settings.openai_compatible_url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json={
                        "model": model, "messages": messages,
                        "stream": False, "temperature": 0,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                options = payload.get("choices") or []
                content = options[0].get("message", {}).get("content", "") if options else ""
            # Bound intermediate notes tightly so a 90-minute transcript reduces
            # rather than expanding across successive map-reduce passes.
            return bounded_model_text(
                content,
                label="event summary",
                max_chars=15_000 if final else 2_500,
            )

        # Long recordings use map-reduce; all requests remain async and bounded.
        if len(texts) == 1:
            return await ask(texts[0], final=True, source_evidence=True)
        partials = [await ask(part, final=False, source_evidence=True) for part in texts]
        for _ in range(8):
            evidence = "\n".join(partials)
            if len(evidence) <= CHUNK_CHARS:
                return await ask(evidence, final=True)
            partials = [await ask(part, final=False) for part in chunks(evidence)]
        raise ValueError("summary did not converge within bounded reduction")
