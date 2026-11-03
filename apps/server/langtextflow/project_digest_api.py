"""Local-operator project digest jobs sharing the session summary resource budget."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from threading import Event

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .post_event_summary import (
    SummaryRequest,
    SummaryReviewRequest,
    generate_summary,
    summary_external_providers,
)
from .project_digests import DigestCapacityError, validate_project_citations
from .storage_guard import StorageCapacityError, ensure_storage_capacity


def install_project_digest_routes(
    app,
    runtime,
    archive,
    settings,
    require_operator,
    jobs,
    start_lock,
    *,
    maximum_jobs,
    timeout_seconds,
):
    store = archive.project_digests
    owned: dict[asyncio.Task, str] = {}
    admissions: dict[asyncio.Task, tuple[Event, asyncio.Event, str]] = {}
    scopes: dict[asyncio.Task, tuple[str, set[str]]] = {}
    closing = False

    @app.get("/api/v1/projects/{project_id}/digests")
    async def listing(request: Request, project_id: str):
        require_operator(request)
        try:
            data = await asyncio.to_thread(store.listing, project_id)
        except KeyError as exc:
            raise HTTPException(404, "project not found") from exc
        return {**data, "summary_external_providers": summary_external_providers(settings)}

    @app.get("/api/v1/projects/{project_id}/digests/{digest_id}")
    async def detail(request: Request, project_id: str, digest_id: str):
        require_operator(request)
        try:
            return await asyncio.to_thread(store.get, project_id, digest_id)
        except KeyError as exc:
            raise HTTPException(404, "project digest not found") from exc

    @app.post("/api/v1/projects/{project_id}/digests", status_code=202)
    async def generate(request: Request, project_id: str, payload: SummaryRequest):
        require_operator(request)
        if payload.transcript_id is not None:
            raise HTTPException(422, "Project source is the explicit per-session summary evidence")
        if (
            payload.provider in summary_external_providers(settings)
            and not payload.allow_external_provider
        ):
            raise HTTPException(400, "external-provider summarization requires explicit opt-in")
        model = payload.model or (
            settings.ollama_correction_model
            if payload.provider == "ollama"
            else settings.openai_compatible_translation_model
        )
        if not model:
            raise HTTPException(400, "configure a summarization model first")
        async with start_lock:
            if closing:
                raise HTTPException(503, "project summary service is shutting down")
            if len(jobs) >= maximum_jobs:
                raise HTTPException(
                    429, "summary capacity reached; retry after another job finishes"
                )
            try:
                abandoned = Event()

                def ensure_space():
                    if abandoned.is_set():
                        raise ValueError("Project request interrupted before admission")
                    ensure_storage_capacity(
                        archive.database_path.parent,
                        required_bytes=1024 * 1024,
                        reserve_bytes=settings.storage_reserve_mb * 1024 * 1024,
                        operation="store project digest evidence",
                    )

                def begin_owned():
                    result, inserted = store.begin(
                        project_id,
                        payload,
                        model,
                        ensure_space=ensure_space,
                    )
                    # This fence runs inside the native thread, even if event-loop
                    # teardown cancels every asyncio task before SQLite returns.
                    if inserted and abandoned.is_set():
                        store.finish(result["digest_id"], error="Project admission interrupted")
                    return result, inserted

                native = asyncio.create_task(asyncio.to_thread(begin_owned))
                decision = asyncio.Event()
                claimed = False

                async def own_admission():
                    try:
                        late_record, late_created = await asyncio.shield(native)
                        await decision.wait()
                        if late_created and not claimed:
                            await asyncio.to_thread(
                                store.finish,
                                late_record["digest_id"],
                                error="Project request interrupted; retry explicitly",
                            )
                    except asyncio.CancelledError:
                        abandoned.set()
                        decision.set()
                        raise
                    except Exception:
                        # Native admission exceptions are mapped by the HTTP task.
                        # A storage failure during cleanup cannot expose its path.
                        logging.getLogger(__name__).warning("Project admission unavailable")

                admission_owner = asyncio.create_task(
                    own_admission(), name="project-admission-owner"
                )
                admissions[admission_owner] = abandoned, decision, project_id
                # Keep a shared slot until SQLite work is either handed to its
                # model worker or independently terminalized after HTTP loss.
                jobs[admission_owner] = ""

                def admission_finished(done):
                    admissions.pop(done, None)
                    jobs.pop(done, None)

                admission_owner.add_done_callback(admission_finished)
                try:
                    record, created = await asyncio.shield(native)
                except BaseException:
                    # Never await native work in cancellation cleanup: another
                    # cancellation must not cancel its independent owner.
                    abandoned.set()
                    decision.set()
                    raise
            except KeyError as exc:
                raise HTTPException(404, "project not found") from exc
            except StorageCapacityError as exc:
                raise HTTPException(507, "insufficient storage for project evidence") from exc
            except DigestCapacityError as exc:
                raise HTTPException(429, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409 if "running" in str(exc) else 422, str(exc)) from exc
            if not created:
                claimed = True
                decision.set()
                jobs.pop(admission_owner, None)
                return record

            async def worker():
                try:

                    async def send_guard():
                        if not await asyncio.to_thread(
                            store.evidence_present,
                            project_id,
                            record["digest_id"],
                            len(record["snapshot"]["sessions"]),
                        ):
                            raise ValueError("Project evidence no longer belongs to this scope")

                    async with asyncio.timeout(timeout_seconds):
                        content = await generate_summary(
                            record["snapshot"]["source"],
                            project_notes=record["snapshot"]["notes"],
                            language=payload.language,
                            provider=payload.provider,
                            model=model,
                            settings=settings,
                            allow_external_provider=payload.allow_external_provider,
                            send_guard=send_guard,
                        )
                    validate_project_citations(record["snapshot"], content)
                    await asyncio.to_thread(store.finish, record["digest_id"], content=content)
                except asyncio.CancelledError:
                    await asyncio.to_thread(
                        store.finish,
                        record["digest_id"],
                        error="Project generation cancelled; retry explicitly",
                    )
                    raise
                except Exception:
                    await asyncio.to_thread(
                        store.finish,
                        record["digest_id"],
                        error="Project model unavailable or invalid evidence; retry later",
                    )

            task = asyncio.create_task(worker(), name=f"project-digest-{record['digest_id']}")
            owned[task] = jobs[task] = record["digest_id"]
            scopes[task] = (
                project_id,
                {entry["session_id"] for entry in record["snapshot"]["sessions"]},
            )
            claimed = True
            decision.set()
            jobs.pop(admission_owner, None)

            def finished(done):
                owned.pop(done, None)
                scopes.pop(done, None)
                jobs.pop(done, None)

            task.add_done_callback(finished)
            return record

    @app.post("/api/v1/projects/{project_id}/digests/{digest_id}/cancel")
    async def cancel(request: Request, project_id: str, digest_id: str):
        require_operator(request)
        async with start_lock:
            try:
                record = await asyncio.to_thread(store.get, project_id, digest_id)
            except KeyError as exc:
                raise HTTPException(404, "project digest not found") from exc
            if record["status"] == "running":
                task = next(
                    (task for task, identifier in owned.items() if identifier == digest_id), None
                )
                if task is None:
                    raise HTTPException(409, "project job is not managed by this instance")
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await asyncio.to_thread(
                    store.finish, digest_id, error="Project generation cancelled; retry explicitly"
                )
            return await asyncio.to_thread(store.get, project_id, digest_id)

    @app.post("/api/v1/projects/{project_id}/digests/{digest_id}/reviews", status_code=201)
    async def review(
        request: Request, project_id: str, digest_id: str, payload: SummaryReviewRequest
    ):
        require_operator(request)
        try:
            return await asyncio.to_thread(store.review, project_id, digest_id, payload)
        except KeyError as exc:
            raise HTTPException(404, "project digest not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/digests/{digest_id}/export")
    async def export(request: Request, project_id: str, digest_id: str):
        require_operator(request)
        try:
            value = await asyncio.to_thread(store.export, project_id, digest_id)
        except KeyError as exc:
            raise HTTPException(404, "project digest not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(
            value,
            headers={
                "Content-Disposition": 'attachment; filename="project-digest-version.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def cancel_related(*, session_id=None, project_id=None, all_jobs=False):
        pending_owners = [
            task
            for task, (stop, decision, pid) in admissions.items()
            if all_jobs or session_id is not None or pid == project_id
        ]
        for task in pending_owners:
            stop, decision, _ = admissions[task]
            stop.set()
            decision.set()
        if pending_owners:
            pending = asyncio.gather(*pending_owners, return_exceptions=True)
            # HTTP and shutdown cancellation cannot cancel an owner while its
            # SQLite thread is still alive. A later shutdown can await it too.
            await asyncio.shield(pending)
        active = [
            (task, identifier)
            for task, identifier in owned.items()
            if all_jobs or scopes[task][0] == project_id or session_id in scopes[task][1]
        ]
        for task, _ in active:
            task.cancel()
        await asyncio.gather(*(task for task, _ in active), return_exceptions=True)
        for task, identifier in active:
            await asyncio.to_thread(
                store.finish,
                identifier,
                error="Project generation interrupted by scope change or shutdown",
            )
            jobs.pop(task, None)
            owned.pop(task, None)
            scopes.pop(task, None)

    @asynccontextmanager
    async def transition(*, session_id=None, project_id=None):
        async with start_lock:
            await cancel_related(session_id=session_id, project_id=project_id)
            yield

    runtime.project_digest_transition = transition

    async def shutdown():
        nonlocal closing
        async with start_lock:
            closing = True
            await cancel_related(all_jobs=True)

    return shutdown
