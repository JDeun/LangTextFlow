"""Operator controls for the independent PCM sink; captions keep their lifecycle."""

from __future__ import annotations

import asyncio
from threading import Event

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .runtime import SessionChangedError


class RecordingControlRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_session_id: str = Field(min_length=1, max_length=128)


def install_recording_routes(app, runtime, require_operator):
    @app.get("/api/v1/session/recording")
    async def status(request: Request):
        require_operator(request)
        return runtime.recording_status()

    async def control(request, payload, action):
        require_operator(request)
        abandoned = Event()

        async def watch():
            while True:
                await asyncio.sleep(0.2)
                if await request.is_disconnected():
                    if action == "start":
                        abandoned.set()
                    return

        watcher = asyncio.create_task(watch(), name="recording-request-disconnect")
        try:
            result = await runtime.control_recording(
                action,
                payload.expected_session_id,
                cancelled=abandoned,
            )
            if result["phase"] == "error":
                raise HTTPException(503, result["error"] or "Recording unavailable")
            return result
        except SessionChangedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(504, "Recording transition timed out; refresh its status") from exc
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    @app.post("/api/v1/session/recording/start")
    async def start(request: Request, payload: RecordingControlRequest):
        return await control(request, payload, "start")

    @app.post("/api/v1/session/recording/stop")
    async def stop(request: Request, payload: RecordingControlRequest):
        return await control(request, payload, "stop")
