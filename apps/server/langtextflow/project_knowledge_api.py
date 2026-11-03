"""Operator-only authoring; no model or external document service is invoked."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Query, Request
from fastapi.responses import Response

from .project_knowledge import KnowledgeConflict, KnowledgeInput


def install_project_knowledge_routes(app, archive, require_operator, transition):
    store = archive.knowledge
    # Scope ownership outlives a disconnected/cancelled HTTP caller until the
    # short SQLite mutation has actually returned and transition locks release.
    owned = set()
    closing = False

    async def mutate(project_id, method, *args):
        if closing:
            raise HTTPException(503, "project materials service is shutting down")
        if len(owned) >= 4:
            raise HTTPException(
                429, "project materials capacity reached; retry when a save finishes"
            )

        async def operation():
            async with transition(project_id=project_id):
                try:
                    return await asyncio.to_thread(method, project_id, *args)
                except KeyError as exc:
                    raise HTTPException(404, "project material not found") from exc
                except KnowledgeConflict as exc:
                    raise HTTPException(409, str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc

        task = asyncio.create_task(operation())
        owned.add(task)

        def finished(done):
            owned.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    @app.get("/api/v1/projects/{project_id}/knowledge")
    async def listing(request: Request, project_id: str):
        require_operator(request)
        try:
            return await asyncio.to_thread(store.listing, project_id)
        except KeyError as exc:
            raise HTTPException(404, "project not found") from exc

    @app.post("/api/v1/projects/{project_id}/knowledge", status_code=201)
    async def create(request: Request, project_id: str, payload: KnowledgeInput):
        require_operator(request)
        return await mutate(project_id, store.save, payload)

    @app.put("/api/v1/projects/{project_id}/knowledge/{knowledge_id}")
    async def update(request: Request, project_id: str, knowledge_id: str, payload: KnowledgeInput):
        require_operator(request)
        return await mutate(project_id, store.save, payload, knowledge_id)

    @app.delete("/api/v1/projects/{project_id}/knowledge/{knowledge_id}", status_code=204)
    async def delete(
        request: Request,
        project_id: str,
        knowledge_id: str,
        expected_revision: int = Query(ge=1),
    ):
        require_operator(request)
        await mutate(project_id, store.delete, knowledge_id, expected_revision)
        return Response(status_code=204)

    async def shutdown():
        nonlocal closing
        closing = True
        if owned:
            await asyncio.shield(asyncio.gather(*tuple(owned), return_exceptions=True))

    return shutdown
