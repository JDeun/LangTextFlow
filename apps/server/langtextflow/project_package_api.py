"""Explicit local private backups, with one independently owned native I/O slot."""

from __future__ import annotations

import asyncio
import sqlite3
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .project_package import ProjectPackageOperation
from .project_package_graph import MAX_PACKAGE_BYTES, PackageError
from .recording_journal import RecoveryStopped
from .storage_guard import StorageCapacityError

LEASE_SECONDS = 15 * 60


def install_project_package_routes(app, runtime, archive, settings, require_operator, summary_lock):
    lock = asyncio.Lock()
    active = None
    pending = {}
    cleanups = {}
    closing = False
    reserve = settings.storage_reserve_mb * 1024 * 1024

    def abandon(op):
        op.stop.set()  # Native work sees loss of ownership even at loop teardown.
        if (lease := getattr(op, "lease", None)) and lease is not asyncio.current_task():
            lease.cancel()
        if op.identifier in cleanups:
            return cleanups[op.identifier]

        async def cleanup():
            nonlocal active
            native = pending.get(op.identifier)
            if native:
                await asyncio.shield(asyncio.gather(native, return_exceptions=True))
            await asyncio.to_thread(op.abort)
            if active is op:
                active = None

        task = asyncio.create_task(cleanup(), name="private-package-cleanup")
        cleanups[op.identifier] = task

        def finished(done):
            cleanups.pop(op.identifier, None)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return task

    async def execute(op, method, *args):
        native = asyncio.create_task(asyncio.to_thread(op.call, method, *args))
        pending[op.identifier] = native

        def finished(done):
            if pending.get(op.identifier) is done:
                pending.pop(op.identifier, None)
            if not done.cancelled():
                done.exception()

        native.add_done_callback(finished)
        try:
            async with asyncio.timeout(600):
                return await asyncio.shield(native)
        except BaseException:
            abandon(op)
            raise

    async def allocate(kind, project_id=None):
        nonlocal active
        async with lock:
            if closing:
                raise HTTPException(503, "Private package service is shutting down")
            if active and (datetime.now(UTC) - active.created_at).total_seconds() > LEASE_SECONDS:
                await asyncio.shield(abandon(active))
            if active:
                raise HTTPException(409, "Download or discard the current private package first")
            active = ProjectPackageOperation(archive.packages, kind, reserve, project_id)
            op = active

            async def expire():
                await asyncio.sleep(LEASE_SECONDS)
                abandon(op)

            op.lease = asyncio.create_task(expire(), name="private-package-expiry")
            return active

    @asynccontextmanager
    async def monitor(op, request):
        async def watch():
            while True:
                await asyncio.sleep(0.2)
                if await request.is_disconnected():
                    abandon(op)
                    return

        watcher = asyncio.create_task(watch(), name="private-package-disconnect")
        try:
            yield
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    def fail(exc):
        if isinstance(exc, PackageError):
            return HTTPException(exc.status, str(exc))
        if isinstance(exc, StorageCapacityError):
            return HTTPException(507, "Insufficient storage for the complete private package")
        if isinstance(exc, TimeoutError):
            return HTTPException(504, "Private package operation exceeded its deadline")
        # Provider/source text, paths and SQLite details never enter responses.
        return HTTPException(422, "Private package is invalid, unavailable, or changed")

    @app.get("/api/v1/project-packages")
    async def status(request: Request):
        require_operator(request)
        if recover := getattr(runtime, "request_recording_recovery", None):
            recover()
        return {
            "imports": await asyncio.to_thread(archive.packages.receipts),
            "repairs": await asyncio.to_thread(archive.packages.repairs),
            "prepared": active.prepared if active and not active.stop.is_set() else None,
        }

    @app.post("/api/v1/projects/{project_id}/package", status_code=201)
    async def prepare(request: Request, project_id: str):
        require_operator(request)
        op = await allocate("export", project_id)
        try:
            async with monitor(op, request):
                async with summary_lock:
                    snapshot = await execute(op, archive.packages.snapshot, project_id, op.stop)
                return await execute(op, op.export, snapshot)
        except asyncio.CancelledError:
            abandon(op)
            raise
        except (
            ValueError,
            OSError,
            sqlite3.Error,
            zipfile.BadZipFile,
            RecoveryStopped,
            TimeoutError,
            OverflowError,
            KeyError,
            TypeError,
        ) as exc:
            abandon(op)
            raise fail(exc) from exc

    @app.post("/api/v1/project-packages/restore", status_code=201)
    async def restore(request: Request):
        require_operator(request)  # Before any uploaded byte or destination allocation.
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(415, "Compressed request bodies are not accepted")
        size = request.headers.get("content-length")
        if size is not None and (
            len(size) > 10 or not size.isdecimal() or int(size) > MAX_PACKAGE_BYTES
        ):
            raise HTTPException(413, "Private project package exceeds 1 GiB")
        op = await allocate("import")
        try:
            async with asyncio.timeout(LEASE_SECONDS):
                await execute(op, op.open_upload)
                async for data in request.stream():
                    # Never hand an unbounded ASGI/network chunk to native I/O.
                    for offset in range(0, len(data), 1024 * 1024):
                        await execute(op, op.write, data[offset : offset + 1024 * 1024])
                async with monitor(op, request):
                    result = await execute(op, op.restore)
            await asyncio.shield(abandon(op))  # Committed files are transferred, ZIP is disposable.
            return result
        except asyncio.CancelledError:
            abandon(op)
            raise
        except Exception as exc:
            abandon(op)
            raise fail(exc) from exc

    @app.delete("/api/v1/project-packages/{export_id}", status_code=204)
    async def discard(request: Request, export_id: str):
        require_operator(request)
        async with lock:
            if active and active.identifier == export_id:
                await asyncio.shield(abandon(active))
        return Response(status_code=204)

    @app.get("/api/v1/project-packages/{export_id}/download")
    async def download(request: Request, export_id: str):
        require_operator(request)
        async with lock:
            op = active
            if not op or op.identifier != export_id or not op.prepared or op.stop.is_set():
                raise HTTPException(404, "Prepared private package not found; prepare it again")
            if getattr(op, "downloading", False):
                raise HTTPException(409, "This private package is already being downloaded")
            op.downloading = True
        try:
            first = await execute(op, op.read)
        except BaseException as exc:
            abandon(op)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise fail(exc) from exc

        async def body():
            chunk = first
            while chunk:
                yield chunk
                chunk = await execute(op, op.read)

        class OwnedDownload(StreamingResponse):
            async def __call__(self, scope, receive, send):
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    # Covers header-send failure before the generator even starts.
                    # Cleanup ownership is independent of disconnect cancellation.
                    abandon(op)

        return OwnedDownload(
            body(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="langtextflow-private-project.zip"',
                "Content-Length": str(op.size),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @asynccontextmanager
    async def transition(*, session_id=None, project_id=None):
        async with lock:
            if (
                active
                and active.kind == "export"
                and (
                    active.project_id == project_id
                    or session_id in active.session_ids
                    or not active.session_ids  # A snapshot is still being admitted.
                )
            ):
                await asyncio.shield(abandon(active))
            yield

    runtime.project_package_transition = transition

    async def shutdown():
        nonlocal closing
        async with lock:
            closing = True
            if active:
                abandon(active)
            if cleanups:
                await asyncio.shield(
                    asyncio.gather(*list(cleanups.values()), return_exceptions=True)
                )

    return shutdown
