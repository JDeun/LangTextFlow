"""Owned SQLite/WAV retention, real scope/cancel/expiry boundaries; no user data."""

from __future__ import annotations

import asyncio
import hashlib
import io
import sqlite3
import sys
import threading
import time
import wave
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from test_project_digests import add_session, seed
from test_project_package import export, prepared, restore

from langtextflow.config import Settings
from langtextflow.models import SessionState
from langtextflow.post_event_api import install_post_event_routes
from langtextflow.project_repository import ProjectInput
from langtextflow.project_retention import (
    RetentionApplyInput,
    RetentionConflict,
    RetentionPolicyInput,
)
from langtextflow.project_retention_api import install_project_retention_routes


def aged(tmp_path):
    history, projects, archive, pid = prepared(tmp_path)
    with archive._connect() as conn:
        conn.execute(
            "UPDATE sessions SET started_at=?,ended_at=?",
            (
                (datetime.now(UTC) - timedelta(days=100)).isoformat(),
                (datetime.now(UTC) - timedelta(days=90)).isoformat(),
            ),
        )
    return history, projects, archive, pid


def enable(archive, pid):
    return archive.retention.save_policy(pid, RetentionPolicyInput(days=30, expected_revision=0))


def payload(preview):
    return RetentionApplyInput(
        operation_id=str(uuid4()),
        expected_revision=preview["policy"]["revision"],
        targets_sha256=preview["targets_sha256"],
    )


def apply(archive, pid, request):
    return archive.retention.apply(pid, request, None, threading.Event(), time.monotonic() + 10)


def test_default_none_explicit_complete_deletion_restart_cleanup_and_idempotent_receipt(tmp_path):
    history, projects, archive, pid = aged(tmp_path)
    assert archive.retention.policy(pid)["days"] is None
    assert archive.retention.preview(pid)["candidates"] == []
    enable(archive, pid)
    preview = archive.retention.preview(pid)
    assert len(preview["candidates"]) == 3 and preview["recording_count"] == 4
    raw, _ = export(archive, pid)
    request = payload(preview)
    receipt = apply(archive, pid, request)
    assert receipt["deleted_session_count"] == 3 and receipt["media_cleanup_pending_count"] == 4
    assert (
        projects.session_ids(pid) == [] and archive.project_digests.listing(pid)["versions"] == []
    )
    assert (
        projects.get(pid) is not None
        and archive.project_digests.archive.knowledge.listing(pid) == []
    )
    assert len(list(archive.audio_root.glob("*.wav"))) == 4  # No unverified unlink in SQL.
    assert apply(archive, pid, request) == receipt
    archive.initialize()
    archive.recording_journal.recover_batch(threading.Event())
    assert not list(archive.audio_root.glob("*.wav"))
    assert archive.retention.receipts(pid)[0]["media_cleanup_pending_count"] == 0
    # Policy is app-local, not transported in original private backup graph.
    projects.delete(pid)
    restore(archive, raw)
    assert archive.retention.policy(pid)["days"] is None
    assert archive.retention.policy(pid)["revision"] == 2 and archive.retention.receipts(pid) == []
    assert len(projects.session_ids(pid)) == 3 and history.get_session("session-0")


@pytest.mark.asyncio
async def test_late_native_policy_save_cannot_resurrect_after_same_id_restore(
    tmp_path, monkeypatch
):
    from langtextflow.project_glossary_repository import ProjectGlossaryRepository

    history, projects, archive, pid = seed(tmp_path, count=0)
    ProjectGlossaryRepository(str(archive.database_path)).initialize()
    raw, _ = export(archive, pid)
    app, _, close, close_archive = app_for(archive, history)
    entered, release = threading.Event(), threading.Event()
    original = archive.retention.save_policy

    def delayed(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(archive.retention, "save_policy", delayed)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://localhost",
            headers={"x-test-operator": "yes"},
        ) as client:
            request = asyncio.create_task(
                client.put(
                    f"/api/v1/projects/{pid}/retention", json={"days": 30, "expected_revision": 0}
                )
            )
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            projects.delete(pid)
            restore(archive, raw)
            release.set()
            assert (await request).status_code == 409
            assert archive.retention.policy(pid)["days"] is None
    finally:
        release.set()
        await close()
        await close_archive()


def test_utc_aware_only_active_recent_and_foreign_scope_never_candidates(tmp_path, monkeypatch):
    from langtextflow import project_retention

    _, projects, archive, pid = seed(tmp_path)
    fixed = datetime(2026, 11, 4, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(project_retention, "datetime", Clock)
    enable(archive, pid)
    with archive._connect() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at='2026-10-05T09:00:00+09:00' WHERE session_id='session-0'"
        )
        conn.execute(
            "UPDATE sessions SET ended_at='2026-01-01T00:00:00' WHERE session_id='session-1'"
        )
        conn.execute("UPDATE sessions SET ended_at='damaged' WHERE session_id='session-2'")
    current = archive.retention.preview(pid)
    assert [r["session_id"] for r in current["candidates"]] == ["session-0"]
    assert current["excluded_unverifiable_count"] == 2
    assert archive.retention.preview(pid, "session-0")["candidates"] == []
    other = projects.create(ProjectInput(title="Other project"))
    assert archive.retention.preview(other.project_id)["candidates"] == []


@pytest.mark.parametrize("mutation", ["policy", "move", "new", "source"])
def test_reviewed_policy_membership_and_late_source_set_must_match(tmp_path, mutation):
    history, projects, archive, pid = aged(tmp_path)
    enable(archive, pid)
    request = payload(archive.retention.preview(pid))
    if mutation == "policy":
        archive.retention.save_policy(pid, RetentionPolicyInput(days=None, expected_revision=1))
    elif mutation == "move":
        projects.assign("session-0", None)
    elif mutation == "new":
        add_session(history, archive, pid, "new-active", ended=False)
    else:
        original = history.segments("session-0")[0]
        history.upsert_segment(
            "session-0", original.model_copy(update={"version": original.version + 1})
        )
    with pytest.raises(RetentionConflict):
        apply(archive, pid, request)
    assert all(history.get_session(f"session-{i}") for i in range(3))


def test_failure_after_first_delete_rolls_back_whole_graph_and_legacy_adoption(tmp_path):
    history, _, archive, pid = aged(tmp_path)
    enable(archive, pid)
    with archive._connect() as conn:
        conn.executescript(
            "CREATE TRIGGER fail_retention BEFORE DELETE ON sessions "
            "WHEN OLD.session_id='session-2' BEGIN "
            "SELECT RAISE(ABORT,'owned failure fixture'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError, match="owned failure fixture"):
        apply(archive, pid, payload(archive.retention.preview(pid)))
    assert len(archive.project_digests.listing(pid)["versions"]) == 1
    assert all(history.get_session(f"session-{i}") for i in range(3))
    with archive._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM recording_journal").fetchone()[0] == 0
    assert (
        archive.retention.receipts(pid) == [] and len(list(archive.audio_root.glob("*.wav"))) == 4
    )


def test_foreign_symlink_and_hash_mismatch_are_preserved_with_durable_diagnostics(tmp_path):
    _, _, archive, pid = aged(tmp_path)
    recordings = archive.recordings("session-0")
    first = archive.recording_path("session-0", recordings[0]["recording_id"])
    outside = tmp_path / "external.wav"
    outside.write_bytes(first.read_bytes())
    first.unlink()
    first.symlink_to(outside)
    second = archive.recording_path("session-0", recordings[1]["recording_id"])
    second.write_bytes(b"foreign replacement")
    unknown = archive.audio_root / "unknown.wav"
    unknown.write_bytes(b"unknown private file")
    enable(archive, pid)
    apply(archive, pid, payload(archive.retention.preview(pid)))
    archive.recording_journal.recover_batch(threading.Event())
    receipt = archive.retention.receipts(pid)[0]
    assert receipt["media_cleanup_pending_count"] == receipt["media_cleanup_blocked_count"] == 2
    assert (
        outside.is_file() and first.is_symlink() and second.read_bytes() == b"foreign replacement"
    )
    assert unknown.read_bytes() == b"unknown private file"


def test_project_membership_bound_rejects_without_any_deletion(tmp_path, monkeypatch):
    from langtextflow import project_retention

    history, _, archive, pid = aged(tmp_path)
    enable(archive, pid)
    reviewed = payload(archive.retention.preview(pid))
    monkeypatch.setattr(project_retention, "MAX_PROJECT_MEMBERS", 2)
    with pytest.raises(ValueError, match="exceeds"):
        archive.retention.preview(pid)
    with pytest.raises(ValueError, match="exceeds"):
        apply(archive, pid, reviewed)
    assert all(history.get_session(f"session-{i}") for i in range(3))
    assert archive.retention.receipts(pid) == []


def batch_fixture(tmp_path, count, media_count):
    history, projects, archive, pid = seed(tmp_path, count=0)
    now = datetime.now(UTC)
    for index in range(count):
        sid = f"old-{index:03}"
        add_session(history, archive, pid, sid, ended=False)
        history.mark_ended(sid, now - timedelta(days=90) + timedelta(seconds=index))
    add_session(history, archive, pid, "active", ended=False)
    foreign = projects.create(ProjectInput(title="Foreign retained project"))
    add_session(history, archive, foreign.project_id, "foreign", ended=False)
    history.mark_ended("foreign", now - timedelta(days=95))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 100)
    raw = stream.getvalue()
    for index in range(media_count):
        name = uuid4().hex + ".wav"
        (archive.audio_root / name).write_bytes(raw)
        archive.insert_recording(
            f"old-{index % count:03}",
            filename=name,
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            duration_seconds=100 / 8000,
            sample_rate=8000,
            channels=1,
        )
    enable(archive, pid)
    return history, projects, archive, pid


@pytest.mark.parametrize("count,media_count,first_count", [(101, 0, 100), (65, 65, 64)])
def test_actual_large_backlog_requires_two_explicit_exact_batches(
    tmp_path, count, media_count, first_count
):
    history, projects, archive, pid = batch_fixture(tmp_path, count, media_count)
    first = archive.retention.preview(pid, "active")
    assert len(first["candidates"]) == first_count
    assert (
        first["expired_session_count"] == count
        and first["remaining_session_count"] == count - first_count
    )
    assert first["remaining_recording_count"] == media_count - min(media_count, 64)
    reviewed = payload(first)
    assert (
        archive.retention.apply(pid, reviewed, "active", threading.Event(), time.monotonic() + 10)[
            "deleted_session_count"
        ]
        == first_count
    )
    assert projects.session_ids(pid) == ["active"] + [
        f"old-{i:03}" for i in range(first_count, count)
    ]
    assert history.get_session("active") and history.get_session("foreign")
    with pytest.raises(RetentionConflict):
        apply(
            archive,
            pid,
            RetentionApplyInput(**{**reviewed.model_dump(), "operation_id": str(uuid4())}),
        )
    second = archive.retention.preview(pid, "active")
    assert (
        len(second["candidates"]) == count - first_count and second["remaining_session_count"] == 0
    )
    apply(archive, pid, payload(second))
    assert projects.session_ids(pid) == ["active"] and history.get_session("foreign")
    for _ in range(10):
        archive.recording_journal.recover_batch(threading.Event())
    assert not list(archive.audio_root.glob("*.wav"))


def test_single_session_over_media_batch_limit_requires_explicit_manual_review(tmp_path):
    history, _, archive, pid = batch_fixture(tmp_path, 1, 65)
    with pytest.raises(ValueError, match="manual session deletion"):
        archive.retention.preview(pid)
    assert history.get_session("old-000") and len(archive.recordings("old-000")) == 65


def test_legacy_adoption_cannot_steal_another_sessions_journal_filename(tmp_path):
    history, projects, archive, pid = aged(tmp_path)
    other = projects.create(ProjectInput(title="Unrelated owner"))
    add_session(history, archive, other.project_id, "foreign")
    recording = archive.recordings("session-0")[0]
    with archive._connect() as conn:
        filename = conn.execute(
            "SELECT filename FROM session_recordings WHERE recording_id=?",
            (recording["recording_id"],),
        ).fetchone()[0]
    archive.recording_journal.reserve(
        "foreign",
        filename=filename,
        **{
            key: recording[key]
            for key in ("size_bytes", "sha256", "duration_seconds", "sample_rate", "channels")
        },
    )
    enable(archive, pid)
    with pytest.raises(RetentionConflict, match="ownership"):
        apply(archive, pid, payload(archive.retention.preview(pid)))
    assert history.get_session("foreign") and all(
        history.get_session(f"session-{i}") for i in range(3)
    )
    assert archive.retention.receipts(pid) == []


def app_for(archive, history):
    app = FastAPI()
    runtime = SimpleNamespace(state=SessionState(), history=history, _lifecycle_lock=asyncio.Lock())

    def operator(request):
        if request.headers.get("x-test-operator") != "yes":
            raise HTTPException(403)

    close_archive = install_post_event_routes(
        app, runtime, archive, Settings(storage_reserve_mb=64), operator
    )

    @asynccontextmanager
    async def transition(**scope):
        async with (
            runtime.project_package_transition(**scope),
            runtime.project_digest_transition(**scope),
        ):
            yield

    close_retention = install_project_retention_routes(app, runtime, archive, operator, transition)
    return app, runtime, close_retention, close_archive


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["http", "explicit"])
async def test_actual_api_authorization_preview_cas_apply_and_cancelled_native_owner(
    tmp_path, monkeypatch, cancellation
):
    history, _, archive, pid = aged(tmp_path)
    app, runtime, close, close_archive = app_for(archive, history)
    entered, release = threading.Event(), threading.Event()
    original = archive.recording_journal._check_metadata

    def hold(row):
        entered.set()
        assert release.wait(3)
        return original(row)

    monkeypatch.setattr(archive.recording_journal, "_check_metadata", hold)
    root = f"/api/v1/projects/{pid}/retention"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost"
        ) as client:
            assert (await client.get(root)).status_code == 403
            client.headers["x-test-operator"] = "yes"
            assert (
                await client.put(root, json={"days": 30, "expected_revision": 0})
            ).status_code == 200
            assert (
                await client.put(root, json={"days": None, "expected_revision": 0})
            ).status_code == 409
            preview = (await client.post(root + "/preview")).json()
            body = payload(preview).model_dump()
            request = asyncio.create_task(client.post(root + "/apply", json=body))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set() and runtime._lifecycle_lock.locked()
            if cancellation == "http":
                request.cancel()
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                assert (
                    await client.post(root + "/cancel", json={"operation_id": "wrong-owner"})
                ).status_code == 409
                assert (
                    await client.post(root + "/cancel", json={"operation_id": body["operation_id"]})
                ).status_code == 202
            assert (await client.post(root + "/apply", json=body)).status_code == 409
            closing = asyncio.create_task(close())
            await asyncio.sleep(0.01)
            assert not closing.done()
            release.set()
            await asyncio.wait_for(closing, 2)
            if cancellation == "explicit":
                assert (await request).status_code == 409
            assert not runtime._lifecycle_lock.locked() and archive.retention.receipts(pid) == []
            assert len(archive.retention.preview(pid)["candidates"]) == 3
    finally:
        release.set()
        await close()
        await close_archive()


@pytest.mark.asyncio
async def test_deadline_keeps_native_slot_until_atomic_rollback(tmp_path, monkeypatch):
    from langtextflow import project_retention_api

    history, _, archive, pid = aged(tmp_path)
    enable(archive, pid)
    app, runtime, close, close_archive = app_for(archive, history)
    entered, release = threading.Event(), threading.Event()
    original = archive.recording_journal._check_metadata

    def hold(row):
        entered.set()
        assert release.wait(3)
        return original(row)

    monkeypatch.setattr(archive.recording_journal, "_check_metadata", hold)
    monkeypatch.setattr(project_retention_api, "APPLY_DEADLINE_SECONDS", 0.05)
    root = f"/api/v1/projects/{pid}/retention"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://localhost",
            headers={"x-test-operator": "yes"},
        ) as client:
            body = payload(archive.retention.preview(pid)).model_dump()
            request = asyncio.create_task(client.post(root + "/apply", json=body))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            await asyncio.sleep(0.08)
            assert not request.done() and runtime._lifecycle_lock.locked()
            assert (await client.post(root + "/apply", json=body)).status_code == 409
            release.set()
            response = await asyncio.wait_for(request, 2)
            assert response.status_code == 504 and not runtime._lifecycle_lock.locked()
            assert (
                archive.retention.receipts(pid) == []
                and len(archive.retention.preview(pid)["candidates"]) == 3
            )
    finally:
        release.set()
        await close()
        await close_archive()


@pytest.mark.asyncio
async def test_apply_reaps_actual_decoder_and_cancels_session_summary_before_delete(
    tmp_path, monkeypatch
):
    from langtextflow import post_event_api, post_event_transcript_api

    history, _, archive, pid = aged(tmp_path)
    app, _, close, close_archive = app_for(archive, history)
    started, cancelled = asyncio.Event(), asyncio.Event()
    processes = []
    native_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await native_spawn(*args, **kwargs)
        processes.append(process)
        return process

    async def summary(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(post_event_api, "generate_summary", summary)
    monkeypatch.setattr(post_event_transcript_api.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(
        post_event_transcript_api,
        "worker_command",
        lambda *args: [
            getattr(sys, "_base_executable", sys.executable),
            "-c",
            "import time; print('{\"progress\":0.1}',flush=True); time.sleep(30)",
        ],
    )
    enable(archive, pid)
    root = f"/api/v1/projects/{pid}/retention"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://localhost",
            headers={"x-test-operator": "yes"},
        ) as client:
            assert (
                await client.post(
                    "/api/v1/history/session-0/summary", json={"model": "owned-test-job"}
                )
            ).status_code == 202
            await asyncio.wait_for(started.wait(), 1)
            recording = archive.recordings("session-0")[0]
            assert (
                await client.post(
                    "/api/v1/history/session-0/transcripts",
                    json={"recording_id": recording["recording_id"]},
                )
            ).status_code == 202
            for _ in range(100):
                if processes:
                    break
                await asyncio.sleep(0.005)
            assert len(processes) == 1 and processes[0].returncode is None
            body = payload(archive.retention.preview(pid)).model_dump()
            response = await client.post(root + "/apply", json=body)
            assert response.status_code == 200, response.text
            assert cancelled.is_set() and processes[0].returncode is not None
            assert await processes[0].wait() == processes[0].returncode
            assert history.get_session("session-0") is None
            # An explicit retry cannot run the deletion again or invent new scope.
            assert (await client.post(root + "/apply", json=body)).json()["operation_id"] == body[
                "operation_id"
            ]
    finally:
        await close()
        await close_archive()
