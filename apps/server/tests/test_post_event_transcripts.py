"""Deterministic dual-pass lifecycle and immutable-source regressions for #104."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_post_event_citation_api import _app

from langtextflow import post_event_api, post_event_transcript_api
from langtextflow.post_event_transcript_api import run_decoder, worker_command
from langtextflow.post_event_transcripts import (
    TranscriptRequest,
    TranscriptReviewRequest,
    validate_segments,
)
from langtextflow.project_archive import build_project_archive_manifest
from langtextflow.project_report import build_project_report
from langtextflow.project_repository import ProjectInput, ProjectRepository

SEGMENTS = [{"segment_id": "offline-1", "start_ms": 0, "end_ms": 1000,
             "text": "운영자가 검토한 원문입니다."}]


def recording(archive, *, duration=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes(16000 * duration))
    raw = buffer.getvalue()
    path = archive.audio_root / "managed.wav"
    path.write_bytes(raw)
    return archive.insert_recording(
        "source-1", filename=path.name, size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), duration_seconds=duration,
        sample_rate=8000, channels=1,
    ), path


def completed(archive, rec):
    result = archive.transcripts.begin("source-1", rec, TranscriptRequest(
        recording_id=rec["recording_id"], model="tiny",
    ))
    archive.transcripts.finish(result["transcript_id"], segments=SEGMENTS,
                               provenance={"model_sha256": "f" * 64})
    return result["transcript_id"]


def settle(archive, tid):
    for _ in range(200):
        result = archive.transcripts.get("source-1", tid)
        if result["status"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("transcription worker did not settle")


def test_review_preserves_live_audio_draft_and_locked_approval(tmp_path):
    app, archive, history = _app(tmp_path)
    rec, path = recording(archive)
    original_audio = path.read_bytes()
    live = history.segments("source-1")
    tid = completed(archive, rec)
    base = f"/api/v1/history/source-1/transcripts/{tid}"
    edited = [{**SEGMENTS[0], "text": "검토 후 바로잡은 원문입니다."}]
    with TestClient(app) as client:
        first = client.post(base + "/reviews", json={"segments": edited})
        assert first.status_code == 201
        assert client.post(base + "/reviews", json={"segments": SEGMENTS}).status_code == 409
        approved = client.post(base + "/reviews", json={
            "segments": edited, "approved": True,
            "expected_review_id": first.json()["review_id"],
        })
        assert approved.status_code == 201
        assert client.post(base + "/reviews", json={
            "segments": SEGMENTS, "approved": True,
            "expected_review_id": approved.json()["review_id"],
        }).status_code == 409
        detail = client.get(base).json()
        assert detail["segments"] == SEGMENTS
        assert detail["review"]["segments"] == edited
        assert client.get("/api/v1/history/other/transcripts/" + tid).status_code == 404
        listing = client.get("/api/v1/history/source-1/transcripts").json()
        assert listing["approved_transcript_id"] == tid
        assert "segments" not in listing["versions"][0]
        exported = client.get(base + "/export")
        assert exported.status_code == 200
        assert exported.json()["version"]["segments"] == SEGMENTS
        assert exported.json()["reviews"][0]["segments"] == edited
        assert len(exported.json()["reviews"]) == 2
    assert path.read_bytes() == original_audio
    assert history.segments("source-1") == live
    with archive._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transcript_reviews").fetchone()[0] == 2


def test_explicit_approved_summary_source_and_new_approval_staleness(monkeypatch, tmp_path):
    app, archive, history = _app(tmp_path)
    projects = ProjectRepository(str(archive.database_path))
    project = projects.create(ProjectInput(title="Reviewed event"))
    projects.assign("source-1", project.project_id)
    rec, _ = recording(archive)
    tid = completed(archive, rec)
    archive.transcripts.review("source-1", tid, TranscriptReviewRequest(
        segments=SEGMENTS, approved=True,
    ))
    seen = []

    async def summarize(source, **kwargs):
        seen.append(source)
        return "검토 요약 [offline-1 at 0s]"

    monkeypatch.setattr(post_event_api, "generate_summary", summarize)
    with TestClient(app) as client:
        response = client.post("/api/v1/history/source-1/summary", json={
            "model": "mock-local", "transcript_id": tid,
        })
        assert response.status_code == 202
        for _ in range(200):
            summary = archive.latest_summary("source-1")
            if summary["status"] != "running":
                break
            time.sleep(0.01)
        assert summary["status"] == "completed"
        assert seen == ["[offline-1 at 0s] {} 운영자가 검토한 원문입니다."]
        source, digest = archive.transcripts.source("source-1", history.segments("source-1"))
        assert "caption-1" in source and digest != summary["transcript_sha256"]
        approve = client.post(
            f"/api/v1/history/source-1/summaries/{summary['summary_id']}/reviews",
            json={"content": summary["content"], "approved": True},
        )
        assert approve.status_code == 201
        assert not client.get("/api/v1/history/source-1/archive").json()["summary_stale"]
        manifest = build_project_archive_manifest(projects, history, archive, project.project_id)
        assert manifest["sessions"][0]["summary"]["transcript_id"] == tid
        assert not manifest["sessions"][0]["summary"]["stale"]
        new_tid = completed(archive, rec)
        archive.transcripts.review("source-1", new_tid, TranscriptReviewRequest(
            segments=SEGMENTS, approved=True,
        ))
        assert client.get("/api/v1/history/source-1/archive").json()["summary_stale"]
        manifest = build_project_archive_manifest(projects, history, archive, project.project_id)
        assert manifest["sessions"][0]["summary"]["stale"]
        report = build_project_report(projects, history, archive, project.project_id)
        assert "STALE" in report
        assert archive.summary_by_id(summary["summary_id"])["review"]["approved"]
        assert archive.summary_by_id(summary["summary_id"])["content"] == summary["content"]
        assert client.post("/api/v1/history/source-1/summary", json={
            "model": "mock-local", "transcript_id": tid,
        }).status_code == 422


def test_bounded_jobs_cancellation_retry_deadline_and_shutdown(monkeypatch, tmp_path):
    app, archive, _ = _app(tmp_path)
    rec, path = recording(archive)
    original = path.read_bytes()
    base = "/api/v1/history/source-1/transcripts"

    async def blocked(command, duration, progress):
        await progress(0.5)
        await asyncio.sleep(999)

    monkeypatch.setattr(post_event_transcript_api, "run_decoder", blocked)
    with TestClient(app) as client:
        payload = {"recording_id": rec["recording_id"], "model": "tiny"}
        result = client.post(base, json=payload)
        assert result.status_code == 202
        tid = result.json()["transcript_id"]
        assert client.post(base, json=payload).status_code == 429
        cancelled = client.post(f"{base}/{tid}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        monkeypatch.setattr(post_event_transcript_api, "TRANSCRIPT_JOB_TIMEOUT_SECONDS", 0.01)
        retry = client.post(base, json=payload)
        assert retry.status_code == 202 and retry.json()["transcript_id"] != tid
        assert settle(archive, retry.json()["transcript_id"])["status"] == "error"
        monkeypatch.setattr(post_event_transcript_api, "TRANSCRIPT_JOB_TIMEOUT_SECONDS", 999)
        shutdown_tid = client.post(base, json=payload).json()["transcript_id"]
    assert archive.transcripts.get("source-1", shutdown_tid)["status"] == "cancelled"
    assert path.read_bytes() == original


def test_completed_job_persists_provenance_and_progress_without_live_rewrite(monkeypatch, tmp_path):
    app, archive, history = _app(tmp_path)
    rec, path = recording(archive)
    source = history.segments("source-1")
    original = path.read_bytes()

    async def decoded(command, duration, progress):
        assert command[-1].endswith("huggingface")
        assert command[-2] == rec["sha256"]
        await progress(0.5)
        return SEGMENTS, {"model_sha256": "f" * 64, "processing_seconds": 0.01}

    monkeypatch.setattr(post_event_transcript_api, "run_decoder", decoded)
    with TestClient(app) as client:
        response = client.post("/api/v1/history/source-1/transcripts", json={
            "recording_id": rec["recording_id"], "model": "tiny",
        })
        assert response.status_code == 202
        result = settle(archive, response.json()["transcript_id"])
        assert result["status"] == "completed" and result["progress"] == 1
        assert result["provenance"]["model_sha256"] == "f" * 64
        assert result["segments"] == SEGMENTS and result["review"] is None
    assert history.segments("source-1") == source
    assert path.read_bytes() == original


def test_restart_retains_versions_and_requires_explicit_retry(tmp_path):
    _, archive, _ = _app(tmp_path)
    rec, _ = recording(archive)
    older = completed(archive, rec)
    running = archive.transcripts.begin("source-1", rec, TranscriptRequest(
        recording_id=rec["recording_id"],
    ))
    archive.initialize()
    assert archive.transcripts.get("source-1", running["transcript_id"])["status"] == "error"
    assert archive.transcripts.get("source-1", older)["segments"] == SEGMENTS
    assert len(archive.transcripts.list("source-1")) == 2


@pytest.mark.parametrize("segments", [
    SEGMENTS * 2,
    [{**SEGMENTS[0], "start_ms": 1001, "end_ms": 1002}],
    [{**SEGMENTS[0], "text": " "}],
    [{**SEGMENTS[0], "start_ms": float("inf")}],
    [{**SEGMENTS[0], "segment_id": "injected at 0s]"}],
    [{**SEGMENTS[0], "start_ms": 900}, {**SEGMENTS[0], "segment_id": "earlier"}],
])
def test_invalid_review_segments_fail_closed(segments):
    with pytest.raises(ValueError):
        validate_segments(segments, 1)


@pytest.mark.parametrize("start,end", [
    (float("nan"), 1), (0, float("inf")), (-0.01, 1), (2, 1), (0, 22.28), (4.1, 4.2),
])
def test_native_decoder_invalid_or_distant_timestamps_fail_closed(start, end):
    from langtextflow.post_event_transcript_worker import window_interval

    with pytest.raises(ValueError, match="outside its audio window"):
        window_interval(start, end, 4)


def test_native_decoder_short_padded_tail_is_explicitly_clipped():
    from langtextflow.post_event_transcript_worker import window_interval

    assert window_interval(1, 4.12, 4) == (1, 4, True)
    assert window_interval(1, 3, 4) == (1, 3, False)


def test_paths_and_recording_ownership_not_client_controlled(tmp_path):
    app, archive, _ = _app(tmp_path)
    rec, path = recording(archive)
    with TestClient(app) as client:
        base = "/api/v1/history/source-1/transcripts"
        assert client.post(base, json={"recording_id": "../../private.wav"}).status_code == 404
        assert client.post(base, json={
            "recording_id": rec["recording_id"], "path": "/private.wav",
        }).status_code == 422
        target = tmp_path / "outside.wav"
        path.rename(target)
        path.symlink_to(target)
        assert client.post(base, json={"recording_id": rec["recording_id"]}).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/history/session/transcripts"),
    ("POST", "/api/v1/history/session/transcripts"),
    ("GET", "/api/v1/history/session/transcripts/version"),
    ("GET", "/api/v1/history/session/transcripts/version/export"),
    ("POST", "/api/v1/history/session/transcripts/version/cancel"),
    ("POST", "/api/v1/history/session/transcripts/version/reviews"),
])
@pytest.mark.parametrize("client_host,headers", [
    ("192.168.1.20", {"X-Forwarded-For": "127.0.0.1"}),
    ("127.0.0.1", {"Origin": "https://hostile.example"}),
])
def test_all_transcript_routes_reject_remote_and_hostile_origin_before_body(
    method, path, client_host, headers,
):
    from langtextflow import main

    client = TestClient(main.app, client=(client_host, 50000))
    response = client.request(method, path, headers=headers, content=b"invalid-json")
    assert response.status_code == 403


def test_session_deletion_cascades_transcripts_reviews_and_approval(tmp_path):
    _, archive, history = _app(tmp_path)
    rec, _ = recording(archive)
    tid = completed(archive, rec)
    archive.transcripts.review("source-1", tid, TranscriptReviewRequest(
        segments=SEGMENTS, approved=True,
    ))
    history.delete_session("source-1")
    assert archive.transcripts.get("source-1", tid) is None
    assert archive.transcripts.approved_id("source-1") is None
    with archive._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transcript_reviews").fetchone()[0] == 0


async def test_cancel_reaps_real_process_before_returning(monkeypatch):
    started = asyncio.Event()
    pid = None
    spawned = []
    create_process = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        process = await create_process(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)

    async def progress(value):
        nonlocal pid
        pid = int(value)
        started.set()

    task = asyncio.create_task(run_decoder([
        # Windows venv python.exe can redirect to a different process; own the sleeper directly.
        getattr(sys, "_base_executable", None) or sys.executable, "-c",
        "import json,os,time; print(json.dumps({'progress':os.getpid()}),flush=True); "
        "time.sleep(99)",
    ], 1, progress))
    await asyncio.wait_for(started.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(spawned) == 1 and spawned[0].pid == pid
    # asyncio's owned process handle observes exit on POSIX and Windows. A PID
    # signal probe is neither portable nor safe against a reused Windows PID.
    assert spawned[0].returncode is not None
    assert await asyncio.wait_for(spawned[0].wait(), 1) == spawned[0].returncode


async def test_worker_parent_pipe_closure_stops_orphaned_native_work(monkeypatch):
    from langtextflow import post_event_transcript_worker

    monkeypatch.setenv("PYTHONPATH", str(Path(post_event_transcript_worker.__file__).parents[1]))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        "import time; from langtextflow import post_event_transcript_worker as w; "
        "print('loaded',flush=True); "
        "w.decode=lambda *args: time.sleep(99); "
        "w.main(['unused.wav','tiny','ko','sha','cache'])",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    assert (await asyncio.wait_for(process.stdout.readline(), 10)).strip() == b"loaded"
    process.stdin.close()
    try:
        assert await asyncio.wait_for(process.wait(), 10) == 1
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_worker_normal_completion_exits_cleanly_with_parent_pipe_still_open(monkeypatch):
    from langtextflow import post_event_transcript_worker

    monkeypatch.setenv("PYTHONPATH", str(Path(post_event_transcript_worker.__file__).parents[1]))
    code = (
        "from langtextflow import post_event_transcript_worker as w; "
        f"messages=[{{'segment':{SEGMENTS[0]!r}}},"
        "{'progress':1},{'complete':{'test':True}}]; "
        "w.decode=lambda *args: [w._emit(item) for item in messages]; "
        "w.main(['unused.wav','tiny','ko','sha','cache'])"
    )
    progress = []

    async def report(value):
        progress.append(value)

    segments, provenance = await asyncio.wait_for(
        run_decoder([sys.executable, "-c", code], 1, report), 10,
    )
    assert segments == SEGMENTS and provenance == {"test": True}
    assert progress == [1]


@pytest.mark.parametrize("text", ["성령 / Holy Spirit / 聖霊 / 🙏", "🙏" * 6000],
                         ids=["multilingual", "maximum-non-bmp"])
async def test_worker_ipc_roundtrips_unicode_through_real_cp1252_stdout(monkeypatch, text):
    from langtextflow import post_event_transcript_worker

    monkeypatch.setenv("PYTHONPATH", str(Path(post_event_transcript_worker.__file__).parents[1]))
    segment = {**SEGMENTS[0], "text": text}
    code = (
        "import io,sys; "
        "sys.stdout=io.TextIOWrapper(sys.stdout.detach(),encoding='cp1252',"
        "errors='strict',newline='\\n',write_through=True); "
        "from langtextflow import post_event_transcript_worker as w; "
        f"messages=[{{'segment':{segment!r}}},"
        "{'progress':1},{'complete':{'test':True}}]; "
        "w.decode=lambda *args: [w._emit(item) for item in messages]; "
        "w.main(['unused.wav','tiny','ko','sha','cache'])"
    )
    progress = []

    async def report(value):
        progress.append(value)

    segments, provenance = await asyncio.wait_for(
        run_decoder([sys.executable, "-c", code], 1, report), 10,
    )
    assert segments == [segment] and provenance == {"test": True}
    assert progress == [1]


async def test_worker_native_decode_can_clone_stdin_while_parent_pipe_is_open(monkeypatch):
    from langtextflow import post_event_transcript_worker

    monkeypatch.setenv("PYTHONPATH", str(Path(post_event_transcript_worker.__file__).parents[1]))
    code = """
import os, time
from langtextflow import post_event_transcript_worker as worker
def decode(*args):
    time.sleep(.1)
    os.close(os.dup(0))
    print('decoded', flush=True)
worker.decode = decode
worker.main(['unused.wav','tiny','ko','sha','cache'])
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert (await asyncio.wait_for(process.stdout.readline(), 3)).strip() == b"decoded"
        assert await asyncio.wait_for(process.wait(), 3) == 0
        assert await process.stderr.read() == b""
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


def test_frozen_worker_dispatch_does_not_start_web_server(monkeypatch):
    from langtextflow import desktop_server, post_event_transcript_worker

    seen = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = worker_command(
        "managed.wav", TranscriptRequest(recording_id="recording"), "sha", "cache",
    )
    assert command[1] == "--post-event-transcript-worker"
    monkeypatch.setattr(sys, "argv", command)
    monkeypatch.setattr(
        desktop_server.multiprocessing, "freeze_support", lambda: seen.append("freeze"),
    )
    monkeypatch.setattr(post_event_transcript_worker, "main", seen.append)
    desktop_server.main()
    assert seen == ["freeze", command[2:]]


@pytest.mark.parametrize("minutes", [30, 60, 90])
def test_long_pcm_uses_bounded_thirty_second_windows(monkeypatch, tmp_path, capsys, minutes):
    from langtextflow import post_event_transcript_worker as worker

    path = tmp_path / "synthetic.wav"
    block = bytes(8000 * 2 * 30)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        for _ in range(minutes * 2):
            audio.writeframesraw(block)
    model_path = tmp_path / "cached-model"
    model_path.mkdir()
    (model_path / "model.bin").write_bytes(b"fake-licensed-test-weights")
    monkeypatch.setattr(worker, "resolve_whisper_model", lambda *args: model_path)
    sizes = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            assert kwargs["device"] == "cpu" and kwargs["local_files_only"]

        def transcribe(self, raw, **kwargs):
            sizes.append(len(raw))
            return iter([SimpleNamespace(text="합성 시험", start=0, end=30.1,
                                         avg_logprob=-0.1, no_speech_prob=0.1)]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", SimpleNamespace(
        decode_audio=lambda buffer: buffer.getvalue(),
    ))
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(
        download_model=lambda *args, **kwargs: str(model_path),
    ))
    monkeypatch.setattr(worker, "version", lambda _: "fake-test")
    with path.open("rb") as audio:
        sha = worker._sha(audio)
    worker.decode(path, "tiny", "ko", sha, str(model_path))
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(sizes) == minutes * 2 and max(sizes) <= len(block) + 44
    assert messages[-2]["progress"] == 1
    assert messages[-1]["complete"]["quality_counters"]["windows"] == minutes * 2
    assert messages[-1]["complete"]["quality_counters"]["clipped_segments"] == minutes * 2
    with path.open("rb") as audio:
        assert worker._sha(audio) == sha
