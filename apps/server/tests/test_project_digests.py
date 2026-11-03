"""Actual SQLite/HTTP project synthesis contracts; no model or audio download."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from langtextflow.config import Settings
from langtextflow.models import CaptionStage, SessionState, StartSessionRequest, TranscriptEvent
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import SummaryRequest, SummaryReviewRequest
from langtextflow.project_digest_api import install_project_digest_routes
from langtextflow.project_digests import validate_project_citations
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


def seed(tmp_path, count=3):
    history = SessionRepository(str(tmp_path / "project.db"))
    history.initialize()
    projects = ProjectRepository(str(history.database_path))
    project = projects.create(ProjectInput(title="Owned synthetic three-session event"))
    archive = PostEventArchive(str(history.database_path))
    archive.initialize()
    for index in range(count):
        add_session(
            history, archive, project.project_id, f"session-{index}", ["ko", "en", "ja"][index % 3]
        )
    return history, projects, archive, project.project_id


def add_session(history, archive, project_id, sid, language="ko", *, ended=True):
    history.create_session(
        SessionState(session_id=sid, join_code="TEST12", started_at=datetime.now(UTC)),
        StartSessionRequest(
            engine="mock",
            project_id=project_id,
            source_language=language,
            target_languages=["en" if language != "en" else "ko"],
        ),
    )
    history.upsert_segment(
        sid,
        TranscriptEvent(
            segment_id="same-segment",
            version=1,
            stage=CaptionStage.COMMITTED,
            committed=True,
            source_language=language,
            text="Corrected display",
            original_text=f"Original speech {sid}",
            start_ms=1200,
            end_ms=2500,
        ),
    )
    if ended:
        history.mark_ended(sid)
        summarize(history, archive, sid)


def summarize(history, archive, sid, *, transcript_id=None):
    source, digest = archive.transcripts.source(sid, history.segments(sid), transcript_id)
    summary = archive.begin_summary(
        sid,
        language="en",
        provider="ollama",
        model="fixture-only",
        transcript_sha256=digest,
        prompt_version="fixture-cited",
        transcript_id=transcript_id,
        approved_transcript_id=archive.transcripts.approved_id(sid),
    )
    archive.finish_summary(
        summary["summary_id"], content=f"Synthetic notes [{source.split(']')[0][1:]}]"
    )
    return archive.latest_summary(sid)


def cited(snapshot):
    return "Chronology from original evidence: " + " ".join(
        item["marker"] for item in snapshot["citations"]
    )


def completed(archive, pid):
    row, created = archive.project_digests.begin(pid, SummaryRequest(language="en"), "fixture")
    assert created
    archive.project_digests.finish(row["digest_id"], content=cited(row["snapshot"]))
    return archive.project_digests.get(pid, row["digest_id"])


def test_three_languages_snapshot_distinguishes_same_segment_and_locks_versions(tmp_path):
    history, projects, archive, pid = seed(tmp_path)
    store = archive.project_digests
    row = completed(archive, pid)
    snapshot = row["snapshot"]
    assert len(snapshot["sessions"]) == 3 and len({r["marker"] for r in snapshot["citations"]}) == 3
    assert (
        "Corrected display" not in snapshot["source"]
        and "Original speech session-2" in snapshot["source"]
    )
    assert store.snapshot(pid)["input_sha256"] == row["input_sha256"]
    again, created = store.begin(pid, SummaryRequest(language="en"), "fixture", allow_new=False)
    assert not created and again["digest_id"] == row["digest_id"]
    with pytest.raises(ValueError, match="every included session"):
        validate_project_citations(snapshot, snapshot["citations"][0]["marker"])
    with pytest.raises(ValueError, match="unknown"):
        validate_project_citations(snapshot, "[same-segment at 1s]")
    first = store.review(pid, row["digest_id"], SummaryReviewRequest(content=cited(snapshot)))
    approved = store.review(
        pid,
        row["digest_id"],
        SummaryReviewRequest(
            content="Reviewed " + cited(snapshot),
            approved=True,
            expected_review_id=first["review_id"],
        ),
    )
    with pytest.raises(ValueError, match="locked"):
        store.review(pid, row["digest_id"], SummaryReviewRequest(content=cited(snapshot)))
    exported = store.export(pid, row["digest_id"])
    assert (
        len(exported["reviews"]) == 2
        and exported["reviews"][-1]["review_id"] == approved["review_id"]
    )
    assert exported["version"]["content"] == row["content"]
    projects.update(pid, ProjectInput(title="Changed event metadata"))
    assert store.get(pid, row["digest_id"])["stale"]
    with pytest.raises(ValueError, match="evidence changed"):
        store.review(pid, row["digest_id"], SummaryReviewRequest(content=cited(snapshot)))
    assert history.segments("session-0")[0].source_text == "Original speech session-0"


@pytest.mark.parametrize(
    "change", ["source", "review", "new_session", "running_finishes", "approval"]
)
def test_input_set_detects_every_child_change(tmp_path, change):
    history, projects, archive, pid = seed(tmp_path)
    if change == "running_finishes":
        add_session(history, archive, pid, "running", ended=False)
    row = completed(archive, pid)
    if change == "source":
        event = history.segments("session-0")[0]
        history.upsert_segment(
            "session-0",
            event.model_copy(
                update={
                    "segment_id": "late-committed-segment",
                    "original_text": "Changed committed evidence",
                    "text": "Changed",
                    "start_ms": 4000,
                    "end_ms": 5000,
                }
            ),
        )
    elif change == "review":
        summary = archive.latest_summary("session-0")
        archive.review_summary(
            "session-0",
            summary["summary_id"],
            content="Reviewed [same-segment at 1s]",
            approved=True,
            expected_review_id=None,
        )
    elif change == "new_session":
        add_session(history, archive, pid, "new")
    elif change == "running_finishes":
        history.mark_ended("running")
        summarize(history, archive, "running")
    elif change == "approval":
        from langtextflow.post_event_transcripts import TranscriptRequest, TranscriptReviewRequest

        recording = archive.insert_recording(
            "session-0",
            filename="fake.wav",
            size_bytes=44,
            sha256="a" * 64,
            duration_seconds=3,
            sample_rate=16000,
            channels=1,
        )
        draft = archive.transcripts.begin(
            "session-0", recording, TranscriptRequest(recording_id=recording["recording_id"])
        )
        seg = {"segment_id": "offline", "start_ms": 1000, "end_ms": 2000, "text": "Reviewed source"}
        archive.transcripts.finish(draft["transcript_id"], segments=[seg])
        archive.transcripts.review(
            "session-0",
            draft["transcript_id"],
            TranscriptReviewRequest(segments=[seg], approved=True),
        )
        summarize(history, archive, "session-0", transcript_id=draft["transcript_id"])
    assert archive.project_digests.get(pid, row["digest_id"])["stale"]
    with pytest.raises(ValueError, match="evidence changed"):
        archive.project_digests.review(
            pid, row["digest_id"], SummaryReviewRequest(content=row["content"])
        )
    if change == "approval":
        newer = completed(archive, pid)
        assert "Reviewed source" in newer["snapshot"]["source"]
        assert newer["snapshot"]["sessions"][0]["transcript_id"] == draft["transcript_id"]


def test_move_redacts_old_project_and_delete_erases_derived_snapshot(tmp_path):
    history, projects, archive, pid = seed(tmp_path)
    row = completed(archive, pid)
    other = projects.create(ProjectInput(title="Another event"))
    projects.assign("session-0", other.project_id)
    old = archive.project_digests.get(pid, row["digest_id"])
    assert old["stale"] and not old["evidence_available"]
    assert old["content"] == "" and old["snapshot"] is None and old["review"] is None
    with pytest.raises(ValueError, match="membership"):
        archive.project_digests.export(pid, row["digest_id"])
    history.delete_session("session-0")
    with pytest.raises(KeyError):
        archive.project_digests.get(pid, row["digest_id"])
    assert history.get_session("session-1") is not None


def test_bounds_fail_closed_and_restart_never_overwrites_approved(tmp_path):
    history, projects, archive, pid = seed(tmp_path)
    row = completed(archive, pid)
    archive.project_digests.review(
        pid, row["digest_id"], SummaryReviewRequest(content=row["content"], approved=True)
    )
    pending, _ = archive.project_digests.begin(pid, SummaryRequest(language="ja"), "fixture")
    archive.initialize()
    assert archive.project_digests.get(pid, pending["digest_id"])["status"] == "error"
    assert archive.project_digests.get(pid, row["digest_id"])["review"]["approved"]
    for index in range(23):
        add_session(history, archive, pid, f"extra-{index}", ended=False)
    state = archive.project_digests.listing(pid)
    assert state["input"] is None and "25 sessions" in state["input_error"]
    assert state["versions"][0]["stale"]
    projects.delete(pid)
    with archive._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_digests").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_digest_reviews").fetchone()[0] == 0


def setup_api(archive, *, settings=None, jobs=None, timeout=600):
    app = FastAPI()

    def guard(request):
        from langtextflow.network import is_loopback_client, operator_origin_allowed

        if not is_loopback_client(request.client.host) or not operator_origin_allowed(
            request.headers.get("origin"),
            allowed_origins=["http://127.0.0.1"],
        ):
            raise HTTPException(403, "operator only")

    jobs = {} if jobs is None else jobs
    close = install_project_digest_routes(
        app,
        SimpleNamespace(),
        archive,
        settings or Settings(storage_reserve_mb=64),
        guard,
        jobs,
        asyncio.Lock(),
        maximum_jobs=2,
        timeout_seconds=timeout,
    )
    return app, jobs, close


async def settled(client, root, identifier):
    for _ in range(100):
        row = (await client.get(f"{root}/{identifier}")).json()
        if row["status"] != "running":
            return row
        await asyncio.sleep(0.005)
    pytest.fail("project job never finished")


@pytest.mark.asyncio
async def test_actual_api_generate_review_export_and_cross_project_authorization(
    tmp_path, monkeypatch
):
    history, projects, archive, pid = seed(tmp_path)
    from langtextflow import project_digest_api

    calls = []

    async def fake(source, **kwargs):
        calls.append((source, kwargs))
        # Real SQL write while provider runs proves inference is outside the snapshot TX.
        if len(calls) == 1:
            projects.update(pid, ProjectInput(title="Metadata changed during generation"))
        return cited(archive.project_digests.snapshot(pid))

    monkeypatch.setattr(project_digest_api, "generate_summary", fake)
    app, jobs, close = setup_api(archive)
    root = f"/api/v1/projects/{pid}/digests"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post(root, json={"language": "en"})
        assert response.status_code == 202
        row = await settled(client, root, response.json()["digest_id"])
        assert row["status"] == "completed" and row["stale"]
        assert calls[0][1]["project_notes"] and "Original speech" in calls[0][0]
        newer = await client.post(root, json={"language": "en"})
        row = await settled(client, root, newer.json()["digest_id"])
        assert not row["stale"]
        review = await client.post(
            f"{root}/{row['digest_id']}/reviews", json={"content": row["content"], "approved": True}
        )
        assert review.status_code == 201
        exported = await client.get(f"{root}/{row['digest_id']}/export")
        assert exported.json()["reviews"][0]["approved"] == 1
        again = await client.post(root, json={"language": "en"})
        assert again.json()["digest_id"] == row["digest_id"] and len(calls) == 2
        other = projects.create(ProjectInput(title="Other"))
        assert (
            await client.get(f"/api/v1/projects/{other.project_id}/digests/{row['digest_id']}")
        ).status_code == 404
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.168.1.4", 3)), base_url="http://local"
    ) as remote:
        for suffix in ["", f"/{row['digest_id']}", f"/{row['digest_id']}/export"]:
            assert (await remote.get(root + suffix)).status_code == 403
        assert (await remote.post(root, json={})).status_code == 403
    await close()
    assert not jobs


@pytest.mark.asyncio
async def test_consent_admission_cancel_deadline_failure_and_shutdown(tmp_path, monkeypatch):
    history, projects, archive, pid = seed(tmp_path)
    from langtextflow import project_digest_api

    calls = []
    gate = asyncio.Event()

    async def blocked(*args, **kwargs):
        calls.append(kwargs)
        await gate.wait()
        raise RuntimeError("secret provider URL and body must stay private")

    monkeypatch.setattr(project_digest_api, "generate_summary", blocked)
    app, jobs, close = setup_api(
        archive, settings=Settings(ollama_url="http://remote.invalid", storage_reserve_mb=64)
    )
    root = f"/api/v1/projects/{pid}/digests"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        assert (await client.get(root)).json()["summary_external_providers"] == [
            "openai-compatible",
            "ollama",
        ]
        assert (await client.post(root, json={})).status_code == 400 and calls == []
        assert archive.project_digests.listing(pid)["versions"] == []
        record = (await client.post(root, json={"allow_external_provider": True})).json()
        await asyncio.sleep(0)
        assert (await client.post(root, json={"allow_external_provider": True})).status_code == 409
        assert (await client.post(f"{root}/{record['digest_id']}/cancel")).json()[
            "status"
        ] == "error"
        assert not jobs
        record = (await client.post(root, json={"allow_external_provider": True})).json()
        gate.set()
        failed = await settled(client, root, record["digest_id"])
        assert failed["status"] == "error" and "secret" not in json.dumps(failed)
        gate.clear()
        record = (await client.post(root, json={"allow_external_provider": True})).json()
        await close()
        assert (await client.get(f"{root}/{record['digest_id']}")).json()[
            "status"
        ] == "error" and not jobs
        assert (await client.post(root, json={"allow_external_provider": True})).status_code == 503
    app, jobs, close = setup_api(archive, timeout=0.01)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        record = (await client.post(root, json={})).json()
        failed = await settled(client, root, record["digest_id"])
        assert failed["status"] == "error" and "secret" not in json.dumps(failed)
    await close()


def test_combined_evidence_cap_rejects_whole_project_without_partial_version(tmp_path):
    history, _, archive, pid = seed(tmp_path, count=2)
    for sid in ["session-0", "session-1"]:
        for number in range(7):
            history.upsert_segment(
                sid,
                TranscriptEvent(
                    segment_id=f"long-{number}",
                    version=1,
                    stage=CaptionStage.COMMITTED,
                    committed=True,
                    source_language="ko",
                    text="A" * 20000,
                    start_ms=10000 + number * 1000,
                ),
            )
        summarize(history, archive, sid)
    with pytest.raises(ValueError, match="240000"):
        archive.project_digests.begin(pid, SummaryRequest(), "fixture")
    state = archive.project_digests.listing(pid)
    assert state["input"] is None and state["versions"] == []
    assert len(history.segments("session-0")) == 8


def test_chronology_orders_timezone_instants_rather_than_iso_text(tmp_path):
    _, _, archive, pid = seed(tmp_path)
    with archive._connect() as conn:
        conn.execute(
            "UPDATE sessions SET started_at=? WHERE session_id='session-0'",
            ("2026-11-03T11:00:00+09:00",),
        )
        conn.execute(
            "UPDATE sessions SET started_at=? WHERE session_id='session-1'",
            ("2026-11-03T03:00:00+00:00",),
        )
        conn.execute(
            "UPDATE sessions SET started_at=? WHERE session_id='session-2'",
            ("2026-11-03T04:00:00+00:00",),
        )
    assert [row["session_id"] for row in archive.project_digests.snapshot(pid)["sessions"]] == [
        "session-0",
        "session-1",
        "session-2",
    ]


@pytest.mark.asyncio
async def test_cancelled_http_admission_owns_late_sqlite_row(tmp_path, monkeypatch):
    _, _, archive, pid = seed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = archive.project_digests.begin

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(archive.project_digests, "begin", delayed)
    app, jobs, close = setup_api(archive)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        task = asyncio.create_task(client.post(f"/api/v1/projects/{pid}/digests", json={}))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            versions = archive.project_digests.listing(pid)["versions"]
            assert len(versions) == 1 and versions[0]["status"] == "error" and not jobs
        finally:
            release.set()
            await close()


@pytest.mark.asyncio
async def test_project_and_session_jobs_share_two_slots_and_deletion_cannot_resurrect(
    tmp_path, monkeypatch
):
    history, projects, archive, pid = seed(tmp_path)
    from langtextflow import post_event_api, project_digest_api

    gate = asyncio.Event()

    async def held(*args, **kwargs):
        await gate.wait()
        return "Model output which must never resurrect deleted data [p0001.s000001 at 1s]"

    monkeypatch.setattr(project_digest_api, "generate_summary", held)
    other = projects.create(ProjectInput(title="Second"))
    third = projects.create(ProjectInput(title="Third"))
    add_session(history, archive, other.project_id, "other-session")
    add_session(history, archive, third.project_id, "third-session")
    app = FastAPI()
    close = post_event_api.install_post_event_routes(
        app,
        SimpleNamespace(history=history, state=SessionState()),
        archive,
        Settings(storage_reserve_mb=64),
        lambda request: None,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        root = f"/api/v1/projects/{pid}/digests"
        record = (await client.post(root, json={})).json()
        second = await client.post(f"/api/v1/projects/{other.project_id}/digests", json={})
        assert second.status_code == 202
        assert (
            await client.post(f"/api/v1/projects/{third.project_id}/digests", json={})
        ).status_code == 429
        # A different model prevents per-session idempotency from bypassing new-job admission.
        assert (
            await client.post("/api/v1/history/third-session/summary", json={"model": "different"})
        ).status_code == 429
        history.delete_session("session-0")
        gate.set()
        await asyncio.sleep(0.05)
        assert (await client.get(f"{root}/{record['digest_id']}")).status_code == 404
        with archive._connect() as conn:
            assert not conn.execute(
                "SELECT 1 FROM project_digests WHERE digest_id=?", (record["digest_id"],)
            ).fetchone()
    await close()


@pytest.mark.asyncio
async def test_project_notes_use_real_bounded_http_prompt_and_canonical_original_citations(
    tmp_path, monkeypatch
):
    _, _, archive, pid = seed(tmp_path)
    from langtextflow import post_event_summary

    snapshot = archive.project_digests.snapshot(pid)
    markers = [item["marker"] for item in snapshot["citations"]]
    bodies = []

    async def provider(request):
        body = json.loads(request.content)
        bodies.append(body)
        task = json.loads(body["messages"][1]["content"])
        assert "UNTRUSTED" in body["messages"][0]["content"]
        assert "EVERY included session" in body["messages"][0]["content"]
        assert task["evidence_kind"] in {"source_transcript", "intermediate_notes"}
        return httpx.Response(200, json={"message": {"content": "Chronology " + " ".join(markers)}})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        post_event_summary.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(provider),
            **kwargs,
        ),
    )
    text = await post_event_summary.generate_summary(
        snapshot["source"],
        project_notes=snapshot["notes"],
        language="en",
        provider="ollama",
        model="fixture-only",
        settings=Settings(),
        allow_external_provider=False,
    )
    validate_project_citations(snapshot, text)
    assert len(bodies) == 3
    source_task, note_task, final_task = [
        json.loads(body["messages"][1]["content"]) for body in bodies
    ]
    assert "Original speech" in source_task["transcript_evidence"]
    assert "existing_session_summary_notes" in note_task["transcript_evidence"]
    assert final_task["task"] == "final_event_summary"
    assert set(re.findall(r"\[p\d+\.s\d+ at \d+s\]", text)) == set(markers)
