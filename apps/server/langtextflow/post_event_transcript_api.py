"""Operator-only bounded local transcription jobs with process-level cancellation."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from contextlib import suppress
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .post_event_transcripts import (
    MAX_SEGMENTS,
    TranscriptRequest,
    TranscriptReviewRequest,
    validate_segments,
)
from .storage_guard import StorageCapacityError, ensure_storage_capacity

TRANSCRIPT_JOB_TIMEOUT_SECONDS = 2 * 3600
MAX_TRANSCRIPT_VERSIONS = 100


def worker_command(path, payload: TranscriptRequest, sha256: str, cache_dir: str) -> list[str]:
    if getattr(sys, "frozen", False):
        prefix = [sys.executable, "--post-event-transcript-worker"]
    else:
        prefix = [sys.executable, "-m", "langtextflow.post_event_transcript_worker"]
    return [*prefix, str(path), payload.model, payload.language, sha256, cache_dir]


async def run_decoder(command: list[str], duration: float, progress) -> tuple[list[dict], dict]:
    launch = asyncio.create_task(asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=128 * 1024,  # JSON escaping of 6000 non-BMP chars needs over 64 KiB.
    ))
    process = None
    segments: list[dict] = []
    provenance = None
    try:
        process = await asyncio.shield(launch)
        if process.stdout is None:
            raise RuntimeError("decoder output pipe unavailable")
        async for line in process.stdout:
            item = json.loads(line)
            if "segment" in item:
                segments.append(item["segment"])
                if len(segments) > MAX_SEGMENTS:
                    raise ValueError("too many segments")
                validate_segments(segments, duration)
            elif "progress" in item:
                await progress(float(item["progress"]))
            elif "complete" in item and len(line) < 8192:
                provenance = item["complete"]
            else:
                raise ValueError("invalid decoder message")
        if await process.wait() != 0 or not isinstance(provenance, dict):
            raise ValueError("local model unavailable or decoder failed")
        return validate_segments(segments, duration), provenance
    finally:
        # Await OS process death before releasing admission, even when native inference hung.
        if process is None:
            while not launch.done():
                try:
                    await asyncio.shield(launch)
                except asyncio.CancelledError:
                    continue
            process = launch.result()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        reap = asyncio.create_task(process.wait())
        while not reap.done():
            try:
                await asyncio.shield(reap)
            except asyncio.CancelledError:
                continue
        if process.stdin:
            process.stdin.close()


def install_transcript_routes(app, runtime, archive, settings, require_operator, finished_session):
    versions = archive.transcripts
    jobs: dict[str, asyncio.Task] = {}
    admission = asyncio.Lock()

    @app.get("/api/v1/history/{session_id}/transcripts")
    async def list_transcripts(request: Request, session_id: str):
        require_operator(request)
        await finished_session(session_id)
        return {
            "versions": await asyncio.to_thread(versions.list, session_id),
            "approved_transcript_id": await asyncio.to_thread(versions.approved_id, session_id),
        }

    @app.get("/api/v1/history/{session_id}/transcripts/{transcript_id}")
    async def transcript_detail(request: Request, session_id: str, transcript_id: str):
        require_operator(request)
        await finished_session(session_id)
        record = await asyncio.to_thread(versions.get, session_id, transcript_id)
        if record is None:
            raise HTTPException(404, "transcript not found")
        return record

    @app.get("/api/v1/history/{session_id}/transcripts/{transcript_id}/export")
    async def export_transcript(request: Request, session_id: str, transcript_id: str):
        require_operator(request)
        await finished_session(session_id)
        try:
            exported = await asyncio.to_thread(versions.export, session_id, transcript_id)
        except KeyError as exc:
            raise HTTPException(404, "transcript not found") from exc
        return JSONResponse(exported, headers={
            "Content-Disposition": f'attachment; filename="transcript-{transcript_id}.json"',
        })

    @app.post("/api/v1/history/{session_id}/transcripts", status_code=202)
    async def request_transcript(request: Request, session_id: str, payload: TranscriptRequest):
        require_operator(request)
        await finished_session(session_id)
        async with admission:
            if jobs:
                raise HTTPException(429, "local transcription capacity reached; retry later")
            if len(await asyncio.to_thread(versions.list, session_id)) >= MAX_TRANSCRIPT_VERSIONS:
                raise HTTPException(409, "transcript version limit reached")
            recordings = await asyncio.to_thread(archive.recordings, session_id)
            recording = next(
                (r for r in recordings if r["recording_id"] == payload.recording_id), None,
            )
            path = await asyncio.to_thread(archive.recording_path, session_id, payload.recording_id)
            if recording is None or path is None:
                raise HTTPException(404, "managed recording not found")
            try:
                ensure_storage_capacity(
                    archive.database_path, required_bytes=4 * 1024 * 1024,
                    reserve_bytes=settings.storage_reserve_mb * 1024 * 1024,
                    operation="store a transcript draft",
                )
                record = await asyncio.to_thread(versions.begin, session_id, recording, payload)
            except StorageCapacityError as exc:
                raise HTTPException(507, "insufficient storage for a transcript draft") from exc
            except sqlite3.IntegrityError as exc:
                raise HTTPException(
                    409, "transcription already running or session deleted",
                ) from exc

            async def worker():
                tid = record["transcript_id"]
                try:
                    async def progress(value):
                        await asyncio.to_thread(versions.progress, tid, value)

                    async with asyncio.timeout(TRANSCRIPT_JOB_TIMEOUT_SECONDS):
                        segments, provenance = await run_decoder(
                            worker_command(
                                path, payload, recording["sha256"],
                                str(Path(settings.model_cache_dir).expanduser().resolve()
                                    / "huggingface"),
                            ),
                            recording["duration_seconds"], progress,
                        )
                    await asyncio.to_thread(versions.finish, tid,
                                            segments=segments, provenance=provenance)
                except asyncio.CancelledError:
                    await asyncio.to_thread(versions.finish, tid, cancelled=True,
                                            error="Local transcription cancelled; retry explicitly")
                    raise
                except Exception:
                    await asyncio.to_thread(
                        versions.finish, tid,
                        error="Local cached ASR model unavailable, deadline exceeded, "
                              "or invalid recording/output; retry after checking model setup",
                    )

            tid = record["transcript_id"]
            task = asyncio.create_task(worker(), name=f"post-event-transcript-{tid}")
            jobs[tid] = task
            task.add_done_callback(lambda _: jobs.pop(tid, None))
            return record

    @app.post("/api/v1/history/{session_id}/transcripts/{transcript_id}/cancel")
    async def cancel_transcript(request: Request, session_id: str, transcript_id: str):
        require_operator(request)
        await finished_session(session_id)
        record = await asyncio.to_thread(versions.get, session_id, transcript_id)
        if record is None:
            raise HTTPException(404, "transcript not found")
        task = jobs.get(transcript_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.to_thread(versions.finish, transcript_id, cancelled=True,
                                    error="Local transcription cancelled; retry explicitly")
        return await asyncio.to_thread(versions.get, session_id, transcript_id)

    @app.post("/api/v1/history/{session_id}/transcripts/{transcript_id}/reviews", status_code=201)
    async def review_transcript(request: Request, session_id: str, transcript_id: str,
                                payload: TranscriptReviewRequest):
        require_operator(request)
        await finished_session(session_id)
        try:
            ensure_storage_capacity(
                archive.database_path, required_bytes=4 * 1024 * 1024,
                reserve_bytes=settings.storage_reserve_mb * 1024 * 1024,
                operation="save a transcript review",
            )
            return await asyncio.to_thread(versions.review, session_id, transcript_id, payload)
        except StorageCapacityError as exc:
            raise HTTPException(507, "insufficient storage for a transcript review") from exc
        except KeyError as exc:
            raise HTTPException(404, "transcript not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    async def shutdown():
        active = list(jobs.items())
        for _, task in active:
            task.cancel()
        await asyncio.gather(*(task for _, task in active), return_exceptions=True)
        for tid, _ in active:
            await asyncio.to_thread(versions.finish, tid, cancelled=True,
                                    error="Local transcription interrupted by application shutdown")

    return shutdown
