"""Actual ASGI streaming, repeated cancellation and mutation coordination."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import ClientDisconnect
from test_project_package import empty, export, prepared

from langtextflow.config import Settings
from langtextflow.project_package import ProjectPackageOperation
from langtextflow.project_package_api import install_project_package_routes


def app_for(archive):
    app = FastAPI()
    runtime = SimpleNamespace()

    def operator(request):
        if request.headers.get("x-test-operator") != "yes":
            raise HTTPException(403, "operator only")

    shutdown = install_project_package_routes(
        app,
        runtime,
        archive,
        Settings(storage_reserve_mb=64),
        operator,
        asyncio.Lock(),
    )
    return app, runtime, shutdown


@pytest.mark.asyncio
async def test_local_explicit_prepare_download_restore_and_lost_ack_receipt(tmp_path):
    _, _, source, pid = prepared(tmp_path / "source")
    app, _, shutdown = app_for(source)
    headers = {"x-test-operator": "yes"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    ) as client:
        response = await client.post(f"/api/v1/projects/{pid}/package")
        assert response.status_code == 201, response.text
        record = response.json()
        assert (await client.post(f"/api/v1/projects/{pid}/package")).status_code == 409
        asset = await client.get(f"/api/v1/project-packages/{record['export_id']}/download")
        assert asset.status_code == 200 and asset.headers["content-type"] == "application/zip"
        assert len(asset.content) == record["size_bytes"]
        for _ in range(100):
            if not list(source.packages.root.iterdir()):
                break
            await asyncio.sleep(0.01)
        assert not list(source.packages.root.iterdir())
    await shutdown()
    _, projects, target = empty(tmp_path / "target")
    app, _, shutdown = app_for(target)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers
    ) as client:
        restored = await client.post("/api/v1/project-packages/restore", content=asset.content)
        assert restored.status_code == 201, restored.text
        assert projects.get(pid)
        listing = (await client.get("/api/v1/project-packages")).json()
        assert listing["imports"][0]["import_id"] == restored.json()["import_id"]
        assert (
            await client.post("/api/v1/project-packages/restore", content=asset.content)
        ).status_code == 409
        await shutdown()


@pytest.mark.asyncio
async def test_operator_guard_before_body_and_declared_actual_limit(tmp_path, monkeypatch):
    _, _, archive = empty(tmp_path)
    app, _, shutdown = app_for(archive)
    sent = False

    async def body():
        nonlocal sent
        sent = True
        yield b"unauthorized private content"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/api/v1/project-packages/restore", content=body())
        ).status_code == 403
        assert not sent and not list(archive.packages.root.iterdir())
        client.headers["x-test-operator"] = "yes"
        assert (
            await client.post(
                "/api/v1/project-packages/restore",
                content=b"x",
                headers={"Content-Length": "9" * 5000},
            )
        ).status_code == 413
        assert (
            await client.post(
                "/api/v1/project-packages/restore",
                content=b"x",
                headers={"Content-Length": "1073741825"},
            )
        ).status_code == 413
        assert (
            await client.post(
                "/api/v1/project-packages/restore",
                content=b"x",
                headers={"Content-Encoding": "gzip"},
            )
        ).status_code == 415
        monkeypatch.setattr("langtextflow.project_package.MAX_PACKAGE_BYTES", 64)

        async def oversized():
            yield b"x" * 40
            yield b"y" * 40

        response = await client.post("/api/v1/project-packages/restore", content=oversized())
        assert response.status_code == 413
    await shutdown()
    assert not list(archive.packages.root.iterdir())


@pytest.mark.asyncio
async def test_repeat_http_and_shutdown_cancel_reaps_late_native_open_without_overlap(
    tmp_path, monkeypatch
):
    _, _, archive = empty(tmp_path)
    app, _, shutdown = app_for(archive)
    opened, release = threading.Event(), threading.Event()
    original = ProjectPackageOperation._new_file
    handles = []

    def late(self, path):
        result = original(self, path)
        handles.append(result)
        opened.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(ProjectPackageOperation, "_new_file", late)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-test-operator": "yes"},
    ) as client:
        task = asyncio.create_task(
            client.post("/api/v1/project-packages/restore", content=b"invalid")
        )
        assert await asyncio.to_thread(opened.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (
            await client.post("/api/v1/project-packages/restore", content=b"x")
        ).status_code == 409
        closing = asyncio.create_task(shutdown())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert not handles[0].closed
        release.set()
        await shutdown()
        assert handles[0].closed and not list(archive.packages.root.iterdir())


@pytest.mark.asyncio
async def test_scope_mutation_cancels_prepared_export_before_deletion(tmp_path):
    _, projects, archive, pid = prepared(tmp_path)
    app, runtime, shutdown = app_for(archive)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-test-operator": "yes"},
    ) as client:
        record = (await client.post(f"/api/v1/projects/{pid}/package")).json()
        async with runtime.project_package_transition(session_id="session-0"):
            projects.assign("session-0", None)
        assert not list(archive.packages.root.iterdir())
        assert (
            await client.get(f"/api/v1/project-packages/{record['export_id']}/download")
        ).status_code == 404
    await shutdown()


@pytest.mark.asyncio
async def test_stream_disconnect_cleans_owned_upload(tmp_path):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, _ = export(source, pid)
    _, projects, target = empty(tmp_path / "target")
    app, _, shutdown = app_for(target)
    messages = iter(
        [
            {"type": "http.request", "body": raw[:1000], "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    replies = []

    async def send(message):
        replies.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/project-packages/restore",
            "raw_path": b"/api/v1/project-packages/restore",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-test-operator", b"yes")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    await shutdown()
    assert projects.list() == [] and not list(target.audio_root.iterdir())
    assert not list(target.packages.root.iterdir())


@pytest.mark.asyncio
async def test_download_disconnect_before_first_body_chunk_releases_owned_lease(tmp_path):
    _, _, archive, pid = prepared(tmp_path)
    app, _, shutdown = app_for(archive)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-test-operator": "yes"},
    ) as client:
        prepared_zip = (await client.post(f"/api/v1/projects/{pid}/package")).json()
        path = f"/api/v1/project-packages/{prepared_zip['export_id']}/download"

        async def receive():
            return {"type": "http.disconnect"}

        async def failed_send(message):
            raise OSError("Owned fake client disconnected before response headers")

        try:
            with pytest.raises(ClientDisconnect):
                await app(
                    {
                        "type": "http",
                        "asgi": {"version": "3.0", "spec_version": "2.4"},
                        "http_version": "1.1",
                        "method": "GET",
                        "scheme": "http",
                        "path": path,
                        "raw_path": path.encode(),
                        "query_string": b"",
                        "root_path": "",
                        "headers": [(b"x-test-operator", b"yes")],
                        "client": ("127.0.0.1", 1234),
                        "server": ("test", 80),
                    },
                    receive,
                    failed_send,
                )
            for _ in range(100):
                if (await client.get("/api/v1/project-packages")).json()["prepared"] is None:
                    break
                await asyncio.sleep(0.01)
            assert not list(archive.packages.root.iterdir())
        finally:
            await shutdown()
