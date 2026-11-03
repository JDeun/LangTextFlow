"""One explicitly requested retention mutation; never a scheduler."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import suppress
from threading import Event

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .project_retention import (
    RetentionApplyInput,
    RetentionConflict,
    RetentionPolicyInput,
    RetentionStopped,
)

APPLY_DEADLINE_SECONDS = 30


class CancelRetention(BaseModel):
    operation_id: str = Field(max_length=64)


def install_project_retention_routes(app, runtime, archive, require_operator, transition):
    store = archive.retention
    active = None
    last = None
    closing = False
    policy_writes = set()

    def active_source():
        stopping = getattr(runtime, "_source_stop", None)
        return (
            runtime.state.session_id
            if runtime.state.running or (stopping and not stopping.done())
            else None
        )

    def fail(exc):
        if isinstance(exc, KeyError):
            return HTTPException(404, "project not found")
        if isinstance(exc, RetentionConflict):
            return HTTPException(409, str(exc))
        if isinstance(exc, RetentionStopped):
            return HTTPException(409, "Retention cancelled; reload preview and deletion receipts")
        if isinstance(exc, TimeoutError):
            return HTTPException(
                504, "Retention deadline exceeded; reload preview and deletion receipts"
            )
        return HTTPException(
            422,
            "Retention data is unavailable, changed, or exceeds safe limits; "
            "reload and review archive diagnostics",
        )

    @app.get("/api/v1/projects/{project_id}/retention")
    async def status(request: Request, project_id: str):
        require_operator(request)
        try:
            policy, receipts = await asyncio.gather(
                asyncio.to_thread(store.policy, project_id),
                asyncio.to_thread(store.receipts, project_id),
            )
        except KeyError as exc:
            raise fail(exc) from exc
        if recover := getattr(runtime, "request_recording_recovery", None):
            recover()
        current = active if active and active["project_id"] == project_id else last
        return {
            "policy": policy,
            "receipts": receipts,
            "operation": {
                k: v
                for k, v in current.items()
                if k in {"operation_id", "project_id", "phase", "error"}
            }
            if current and current["project_id"] == project_id
            else None,
        }

    @app.put("/api/v1/projects/{project_id}/retention")
    async def policy(request: Request, project_id: str, payload: RetentionPolicyInput):
        require_operator(request)
        if closing or len(policy_writes) >= 4:
            raise HTTPException(503, "Retention settings are busy or shutting down; retry later")
        task = asyncio.create_task(asyncio.to_thread(store.save_policy, project_id, payload))
        policy_writes.add(task)

        def done(finished):
            policy_writes.discard(finished)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(done)
        try:
            return await asyncio.shield(task)
        except (KeyError, ValueError, sqlite3.Error) as exc:
            raise fail(exc) from exc

    @app.post("/api/v1/projects/{project_id}/retention/preview")
    async def preview(request: Request, project_id: str):
        require_operator(request)
        try:
            return await asyncio.to_thread(store.preview, project_id, active_source())
        except (KeyError, ValueError, sqlite3.Error, TimeoutError) as exc:
            raise fail(exc) from exc

    @app.post("/api/v1/projects/{project_id}/retention/apply")
    async def apply(request: Request, project_id: str, payload: RetentionApplyInput):
        nonlocal active, last
        require_operator(request)
        if closing:
            raise HTTPException(503, "Retention service is shutting down")
        if active:
            raise HTTPException(
                409, "Another retention operation is still finishing; reload status"
            )
        operation = {
            "operation_id": payload.operation_id,
            "project_id": project_id,
            "phase": "running",
            "error": None,
            "stop": Event(),
        }
        active = operation
        deadline = time.monotonic() + APPLY_DEADLINE_SECONDS
        native = None

        async def run_owned():
            nonlocal active, last, native
            try:
                completed = await asyncio.to_thread(store.completed, project_id, payload)
                if completed:
                    operation["phase"] = "completed"
                    return completed
                if operation["stop"].is_set():
                    raise RetentionStopped
                async with asyncio.timeout(APPLY_DEADLINE_SECONDS):
                    async with runtime._lifecycle_lock, transition(project_id=project_id):
                        # Refresh candidates after obtaining both transition locks.
                        # The native write transaction repeats this exact check.
                        snapshot = await asyncio.to_thread(
                            store.preview, project_id, active_source()
                        )
                        if operation["stop"].is_set():
                            raise RetentionStopped
                        if (
                            snapshot["policy"]["revision"] != payload.expected_revision
                            or snapshot["targets_sha256"] != payload.targets_sha256
                        ):
                            raise RetentionConflict(
                                "Retention policy or candidates changed; preview again"
                            )
                        ids = {item["session_id"] for item in snapshot["candidates"]}
                        async with runtime.archive_session_transition(ids):
                            native = asyncio.create_task(
                                asyncio.to_thread(
                                    store.apply,
                                    project_id,
                                    payload,
                                    active_source(),
                                    operation["stop"],
                                    deadline,
                                )
                            )
                            try:
                                result = await asyncio.shield(native)
                            except BaseException:
                                operation["stop"].set()
                                # Keep the scope/native slot until the real SQL
                                # transaction has committed or rolled back.
                                while not native.done():
                                    with suppress(asyncio.CancelledError):
                                        await asyncio.shield(
                                            asyncio.gather(native, return_exceptions=True)
                                        )
                                if not native.cancelled() and native.exception() is None:
                                    result = native.result()  # Commit won the cancellation race.
                                else:
                                    raise
                operation["phase"] = "completed"
                return result
            except BaseException as exc:
                operation["stop"].set()
                operation["phase"] = (
                    "cancelled"
                    if isinstance(exc, (RetentionStopped, asyncio.CancelledError))
                    else "error"
                )
                operation["error"] = fail(exc).detail
                raise
            finally:
                last = operation
                if active is operation:
                    active = None
                if recover := getattr(runtime, "request_recording_recovery", None):
                    recover()

        owner = asyncio.create_task(run_owned(), name="project-retention")
        operation["owner"] = owner
        owner.add_done_callback(lambda done: None if done.cancelled() else done.exception())

        async def watch():
            while True:
                await asyncio.sleep(0.2)
                if await request.is_disconnected():
                    operation["stop"].set()
                    return

        watcher = asyncio.create_task(watch())
        try:
            return await asyncio.shield(owner)
        except asyncio.CancelledError:
            operation["stop"].set()
            raise
        except (KeyError, ValueError, sqlite3.Error, RetentionStopped, TimeoutError) as exc:
            raise fail(exc) from exc
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    @app.post("/api/v1/projects/{project_id}/retention/cancel", status_code=202)
    async def cancel(request: Request, project_id: str, payload: CancelRetention):
        require_operator(request)
        if (
            not active
            or active["project_id"] != project_id
            or active["operation_id"] != payload.operation_id
        ):
            raise HTTPException(
                409, "Retention operation changed or already finished; reload status"
            )
        active["stop"].set()
        return {"operation_id": payload.operation_id, "cancel_requested": True}

    async def shutdown():
        nonlocal closing
        closing = True
        if active:
            active["stop"].set()
            await asyncio.shield(asyncio.gather(active["owner"], return_exceptions=True))
        if policy_writes:
            await asyncio.shield(asyncio.gather(*tuple(policy_writes), return_exceptions=True))

    return shutdown
