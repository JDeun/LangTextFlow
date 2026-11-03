"""Real SQLite/source guards/package history for authored materials, no models."""

import asyncio
import hashlib
import io
import json
import threading
import zipfile
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from test_project_digests import cited, seed, setup_api
from test_project_package import empty, export, prepared, restore, rewrite

from langtextflow.post_event_summary import SummaryRequest, SummaryReviewRequest
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.project_knowledge import KnowledgeConflict, KnowledgeInput
from langtextflow.project_knowledge_api import install_project_knowledge_routes
from langtextflow.project_package_graph import PackageError, canonical, parse_json


def note(**changes):
    return KnowledgeInput(
        kind="decision", title="Operator decision", text="성령 / 原文 🙏", **changes
    )


def digest(archive, pid):
    version, _ = archive.project_digests.begin(pid, SummaryRequest(), "fixture")
    content = cited(version["snapshot"])
    archive.project_digests.finish(version["digest_id"], content=content)
    archive.project_digests.review(
        pid, version["digest_id"], SummaryReviewRequest(content=content, approved=True)
    )
    return version


def test_revision_hash_stale_original_scope_and_delete(tmp_path):
    _, projects, archive, pid = seed(tmp_path)
    first = archive.knowledge.save(pid, note())
    old = digest(archive, pid)
    assert old["snapshot"]["knowledge"] == [first]
    assert first["text"] not in old["snapshot"]["source"]
    assert "untrusted_operator_authored_project_materials" in old["snapshot"]["notes"]
    new = archive.knowledge.save(pid, note(expected_revision=1), first["knowledge_id"])
    assert new["revision"] == 2 and new["content_sha256"] == first["content_sha256"]
    assert archive.project_digests.get(pid, old["digest_id"])["stale"]
    with pytest.raises(KnowledgeConflict):
        archive.knowledge.save(pid, note(expected_revision=1), first["knowledge_id"])
    with pytest.raises(KnowledgeConflict):
        archive.knowledge.delete(pid, first["knowledge_id"], 1)
    # A stale approved digest still preserves its original reviewed input.
    assert archive.project_digests.get(pid, old["digest_id"])["snapshot"]["knowledge"][0] == first
    archive.knowledge.delete(pid, first["knowledge_id"], 2)
    assert archive.project_digests.listing(pid)["versions"] == []
    assert len(projects.session_ids(pid)) == 3


def test_provider_guard_checks_notes_between_every_send(tmp_path):
    _, _, archive, pid = seed(tmp_path)
    first = archive.knowledge.save(pid, note())
    active, _ = archive.project_digests.begin(pid, SummaryRequest(), "fixture")

    def check():
        return archive.project_digests.evidence_present(pid, active["digest_id"], 3)

    assert check()
    archive.knowledge.save(pid, note(expected_revision=1), first["knowledge_id"])
    assert not check()


def test_bounded_input_and_project_isolation(tmp_path):
    _, _, archive, pid = seed(tmp_path, count=0)
    for changes in ({"text": " "}, {"text": "\x00"}, {"text": "x" * 20001}, {"done": True}):
        with pytest.raises(ValidationError):
            KnowledgeInput(kind="decision", title="test", **{"text": "test", **changes})
    first = archive.knowledge.save(pid, note())
    with pytest.raises(KeyError):
        archive.knowledge.save("foreign", note(expected_revision=1), first["knowledge_id"])
    for _ in range(5):
        archive.knowledge.save(pid, KnowledgeInput(kind="handout", title="full", text="x" * 20000))
    with pytest.raises(ValueError, match="120000"):
        archive.knowledge.save(
            pid, KnowledgeInput(kind="handout", title="too much", text="x" * 20000)
        )


def test_package_v3_roundtrip_preserves_materials_and_stale_approved_history(tmp_path):
    _, _, archive, pid = seed(tmp_path / "source")
    ProjectGlossaryRepository(str(archive.database_path)).initialize()
    first = archive.knowledge.save(pid, note())
    old = digest(archive, pid)
    archive.knowledge.save(pid, note(expected_revision=1), first["knowledge_id"])
    raw, graph = export(archive, pid)
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        assert parse_json(package.read("manifest.json"))["version"] == 3
    _, _, target = empty(tmp_path / "target")
    restore(target, raw)
    assert target.knowledge.listing(pid) == archive.knowledge.listing(pid)
    restored = target.project_digests.get(pid, old["digest_id"])
    assert restored["stale"] and restored["review"]["approved"]
    with target._connect() as conn:
        assert (
            conn.execute("SELECT snapshot_json FROM project_digests").fetchone()[0]
            == graph["tables"]["project_digests"][0]["snapshot_json"]
        )

    # Foreign IDs remain rejected, even when the attacker recomputes snapshot hashes.
    def foreign(g):
        snap = parse_json(g["tables"]["project_digests"][0]["snapshot_json"])
        snap["knowledge"][0]["knowledge_id"] = "foreign"
        snap["input_sha256"] = hashlib.sha256(
            canonical({k: v for k, v in snap.items() if k != "input_sha256"}).encode()
        ).hexdigest()
        g["tables"]["project_digests"][0]["input_sha256"] = snap["input_sha256"]
        g["tables"]["project_digests"][0]["snapshot_json"] = canonical(snap)

    _, _, rejected = empty(tmp_path / "rejected")
    with pytest.raises(PackageError, match="foreign or missing"):
        restore(rejected, rewrite(raw, graph_change=foreign))


@pytest.mark.parametrize("version", [1, 2])
def test_original_package_v1_v2_bytes_verified_before_explicit_empty_migration(tmp_path, version):
    _, _, archive, pid = prepared(tmp_path / "source")
    with archive._connect() as conn:
        conn.execute("UPDATE session_recordings SET start_offset_ms=1250.0625")
    raw, _ = export(archive, pid)

    def legacy(entries):
        g = parse_json(entries["graph.json"])
        g["tables"].pop("project_knowledge")
        if version == 1:
            for row in g["tables"]["session_recordings"]:
                row.pop("start_offset_ms")
        entries["graph.json"] = canonical(g).encode()
        m = parse_json(entries["manifest.json"])
        m.update(
            version=version,
            graph_sha256=hashlib.sha256(entries["graph.json"]).hexdigest(),
            graph_size_bytes=len(entries["graph.json"]),
        )
        m["counts"].pop("project_knowledge")
        entries["manifest.json"] = canonical(m).encode()

    _, _, target = empty(tmp_path / "target")
    restore(target, rewrite(raw, entry_change=legacy))
    assert target.knowledge.listing(pid) == []
    expected = None if version == 1 else 1250.0625
    assert all(row["start_offset_ms"] == expected for row in target.recordings("session-0"))


@pytest.mark.asyncio
async def test_api_auth_optimistic_edits_and_transition_before_mutation(tmp_path):
    _, _, archive, pid = seed(tmp_path)
    app = FastAPI()
    entered = []

    @asynccontextmanager
    async def transition(**scope):
        entered.append(scope)
        yield

    def operator(request):
        if request.headers.get("x-test-operator") != "yes":
            raise HTTPException(403)

    shutdown = install_project_knowledge_routes(app, archive, operator, transition)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost"
    ) as client:
        url = f"/api/v1/projects/{pid}/knowledge"
        assert (await client.get(url)).status_code == 403
        client.headers["x-test-operator"] = "yes"
        response = await client.post(url, json=note().model_dump())
        assert response.status_code == 201
        item = response.json()
        path = f"{url}/{item['knowledge_id']}"
        assert (await client.put(path, json=note().model_dump())).status_code == 409
        assert (
            await client.put(path, json=note(expected_revision=1).model_dump())
        ).status_code == 200
        assert (await client.delete(path + "?expected_revision=1")).status_code == 409
        assert (await client.delete(path + "?expected_revision=2")).status_code == 204
        assert entered == [{"project_id": pid}] * 5
    await shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["update", "delete"])
async def test_each_actual_provider_send_checks_material_revision(tmp_path, monkeypatch, mutation):
    from langtextflow import post_event_summary

    _, _, archive, pid = seed(tmp_path)
    material = archive.knowledge.save(pid, note())
    first, release = asyncio.Event(), asyncio.Event()
    sends = []
    content = cited(archive.project_digests.snapshot(pid))

    async def provider(request):
        sends.append(json.loads(request.content))
        first.set()
        await release.wait()
        return httpx.Response(200, json={"message": {"content": content}})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        post_event_summary.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(provider), **kw),
    )
    app, jobs, shutdown = setup_api(archive)
    try:
        async with client_type(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1"
        ) as client:
            assert (
                await client.post(f"/api/v1/projects/{pid}/digests", json={})
            ).status_code == 202
            await asyncio.wait_for(first.wait(), 1)
            # Direct repository mutation bypasses the API cancellation hook;
            # the shared per-send guard must independently revoke disclosure.
            if mutation == "update":
                archive.knowledge.save(pid, note(expected_revision=1), material["knowledge_id"])
            else:
                archive.knowledge.delete(pid, material["knowledge_id"], 1)
            release.set()
            for _ in range(100):
                if not jobs:
                    break
                await asyncio.sleep(0.005)
            assert not jobs and len(sends) == 1
            assert "UNTRUSTED CONTEXT" in sends[0]["messages"][0]["content"]
    finally:
        release.set()
        await shutdown()


@pytest.mark.asyncio
async def test_cancelled_http_mutation_retains_scope_until_native_commit(tmp_path, monkeypatch):
    _, _, archive, pid = seed(tmp_path)
    app, scope_lock = FastAPI(), asyncio.Lock()
    native_entered, release = threading.Event(), threading.Event()
    original = archive.knowledge.save

    def slow(*args):
        native_entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(archive.knowledge, "save", slow)

    @asynccontextmanager
    async def transition(**scope):
        async with scope_lock:
            yield

    shutdown = install_project_knowledge_routes(app, archive, lambda _: None, transition)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1"
        ) as client:
            request = asyncio.create_task(
                client.post(
                    f"/api/v1/projects/{pid}/knowledge",
                    json=note().model_dump(),
                )
            )
            for _ in range(100):
                if native_entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert native_entered.is_set()
            request.cancel()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert scope_lock.locked()
            closing = asyncio.create_task(shutdown())
            await asyncio.sleep(0.01)
            assert not closing.done()
            release.set()
            await asyncio.wait_for(closing, 1)
            assert not scope_lock.locked() and len(archive.knowledge.listing(pid)) == 1
    finally:
        release.set()
        await shutdown()
