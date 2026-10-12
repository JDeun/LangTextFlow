"""Operator-only archive REST routes; no audio/summary is exposed to LAN audience."""
from __future__ import annotations

import asyncio
import hashlib
import os
import wave
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from .config import Settings
from .post_event_archive import PostEventArchive
from .post_event_summary import (
    PROMPT_VERSION,
    SummaryRequest,
    generate_summary,
    transcript_source,
)
from .runtime import CaptionRuntime
from .storage_guard import StorageCapacityError, ensure_storage_capacity

MAX_RECORDING_BYTES = 512 * 1024 * 1024


def install_post_event_routes(
    app: FastAPI,
    runtime: CaptionRuntime,
    archive: PostEventArchive,
    settings: Settings,
    require_operator,
) -> None:
    """Register archive functionality without changing existing session API shapes."""
    summary_jobs: set[asyncio.Task] = set()

    async def finished_session(session_id: str):
        session = await asyncio.to_thread(runtime.history.get_session, session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.ended_at is None or (
            runtime.state.running and runtime.state.session_id == session_id
        ):
            raise HTTPException(409, "stop the session before archiving")
        return session

    @app.get("/api/v1/history/{session_id}/archive")
    async def event_archive_detail(request: Request, session_id: str) -> dict:
        require_operator(request)
        await finished_session(session_id)
        recordings, summary = await asyncio.gather(
            asyncio.to_thread(archive.recordings, session_id),
            asyncio.to_thread(archive.latest_summary, session_id),
        )
        return {"recordings": recordings, "summary": summary}

    @app.post("/api/v1/history/{session_id}/recordings", status_code=201)
    async def upload_recording(request: Request, session_id: str) -> dict:
        """Import an already captured, authorized PCM WAV file with bounded streaming."""
        require_operator(request)
        await finished_session(session_id)
        if request.headers.get("content-type", "").split(";")[0].strip() not in {
            "audio/wav", "audio/x-wav", "audio/wave",
        }:
            raise HTTPException(415, "expected PCM WAV (audio/wav)")
        declared_size = request.headers.get("content-length")
        if declared_size:
            try:
                size = int(declared_size)
            except ValueError as exc:
                raise HTTPException(400, "invalid content-length") from exc
            if size > MAX_RECORDING_BYTES:
                raise HTTPException(413, "recording exceeds upload limit")
        ensure_storage_capacity(
            archive.audio_root,
            required_bytes=min(int(declared_size or MAX_RECORDING_BYTES), MAX_RECORDING_BYTES),
            reserve_bytes=settings.storage_reserve_mb * 1024 * 1024,
            operation="store an event recording",
        )
        filename = f"{uuid4().hex}.wav"
        path = archive.audio_root / filename
        total = 0
        digest = hashlib.sha256()
        try:
            # Exclusive create/0600; no user-controlled path or filename.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                async for part in request.stream():
                    total += len(part)
                    if total > MAX_RECORDING_BYTES:
                        raise HTTPException(413, "recording exceeds upload limit")
                    digest.update(part)
                    await asyncio.to_thread(output.write, part)
            with wave.open(str(path), "rb") as wav:
                channels = wav.getnchannels()
                sample_rate = wav.getframerate()
                samples = wav.getnframes()
                if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
                    raise HTTPException(422, "expected 16-bit uncompressed PCM WAV")
                if channels not in {1, 2} or not (8000 <= sample_rate <= 48000):
                    raise HTTPException(422, "unsupported WAV channels or sample rate")
                if samples == 0 or samples / sample_rate > 4 * 60 * 60:
                    raise HTTPException(422, "recording length must be between 0 and 4 hours")
                # Reject truncated payloads whose declared WAV frames are absent.
                if len(wav.readframes(samples)) != samples * channels * 2:
                    raise HTTPException(422, "incomplete WAV audio frames")
                duration = samples / sample_rate
            return await asyncio.to_thread(
                archive.insert_recording,
                session_id,
                filename=filename,
                size_bytes=total,
                sha256=digest.hexdigest(),
                duration_seconds=duration,
                sample_rate=sample_rate,
                channels=channels,
            )
        except HTTPException:
            path.unlink(missing_ok=True)
            raise
        except (OSError, EOFError, wave.Error, ValueError, StorageCapacityError) as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(422, "invalid or unwritable WAV recording") from exc
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.get("/api/v1/history/{session_id}/recordings/{recording_id}/audio")
    async def download_recording(
        request: Request, session_id: str, recording_id: str,
    ):
        require_operator(request)
        await finished_session(session_id)
        path = await asyncio.to_thread(archive.recording_path, session_id, recording_id)
        if path is None:
            raise HTTPException(404, "recording not found")
        return FileResponse(
            path, media_type="audio/wav",
            filename=f"langtextflow-{session_id}.wav",
            content_disposition_type="inline",
        )

    @app.post("/api/v1/history/{session_id}/summary", status_code=202)
    async def request_summary(
        request: Request, session_id: str, payload: SummaryRequest,
    ) -> dict:
        require_operator(request)
        await finished_session(session_id)
        if payload.provider == "openai-compatible" and not payload.allow_external_provider:
            raise HTTPException(
                400, "external-compatible summarization requires explicit opt-in",
            )
        model = payload.model or (
            settings.ollama_correction_model
            if payload.provider == "ollama"
            else settings.openai_compatible_translation_model
        )
        if not model:
            raise HTTPException(400, "configure a summarization model first")
        segments = await asyncio.to_thread(runtime.history.segments, session_id)
        try:
            source, revision = transcript_source(segments)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        previous = await asyncio.to_thread(archive.latest_summary, session_id)
        if previous and previous["status"] == "running":
            raise HTTPException(409, "summary generation already running")
        if (
            previous and previous["status"] == "completed"
            and previous["transcript_sha256"] == revision
            and previous["language"] == payload.language
            and previous["provider"] == payload.provider
            and previous["model"] == model
            and previous["prompt_version"] == PROMPT_VERSION
        ):
            return previous
        record = await asyncio.to_thread(
            archive.begin_summary,
            session_id,
            language=payload.language,
            provider=payload.provider,
            model=model,
            transcript_sha256=revision,
            prompt_version=PROMPT_VERSION,
        )

        async def worker() -> None:
            try:
                content = await generate_summary(
                    source, language=payload.language, provider=payload.provider,
                    model=model, settings=settings,
                    allow_external_provider=payload.allow_external_provider,
                )
                await asyncio.to_thread(
                    archive.finish_summary, record["summary_id"], content=content,
                )
            except Exception:
                # Never return remote provider response bodies or credentials to the UI.
                await asyncio.to_thread(
                    archive.finish_summary, record["summary_id"],
                    error="Summary model unavailable or returned invalid output; retry later",
                )

        task = asyncio.create_task(
            worker(), name=f"post-event-summary-{record['summary_id']}",
        )
        summary_jobs.add(task)
        task.add_done_callback(summary_jobs.discard)
        return record
