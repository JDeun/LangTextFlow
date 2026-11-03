"""Private backup roundtrip and hostile ZIP/OS boundaries; synthetic local data only."""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import threading
import time
import wave
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_project_digests import seed, summarize

from langtextflow.models import GlossaryEntry
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import SummaryRequest, SummaryReviewRequest
from langtextflow.post_event_transcripts import TranscriptRequest, TranscriptReviewRequest
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.project_package import ProjectPackageOperation
from langtextflow.project_package_graph import PackageError, canonical, parse_json


def prepared(tmp_path, *, full=True):
    history, projects, archive, pid = seed(tmp_path)
    glossary = ProjectGlossaryRepository(str(history.database_path))
    glossary.initialize()
    glossary.create(pid, GlossaryEntry(term="owned", source_language="en", canonical="owned"))
    if full:
        for index in range(4):
            sid = f"session-{index % 3}"
            audio = io.BytesIO()
            with wave.open(audio, "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8000)
                writer.writeframes(bytes([index, 0]) * 8000)
            raw = audio.getvalue()
            filename = f"{uuid4().hex}.wav"
            path = archive.audio_root / filename
            path.write_bytes(raw)
            rec = archive.insert_recording(
                sid,
                filename=filename,
                size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                duration_seconds=1,
                sample_rate=8000,
                channels=1,
            )
            tid = archive.transcripts.begin(
                sid, rec, TranscriptRequest(recording_id=rec["recording_id"])
            )["transcript_id"]
            segments = [
                {
                    "segment_id": "offline-1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "Owned synthetic draft",
                }
            ]
            archive.transcripts.finish(
                tid, segments=segments, provenance={"model_sha256": "f" * 64}
            )
            if index < 3:
                archive.transcripts.review(
                    sid, tid, TranscriptReviewRequest(segments=segments, approved=True)
                )
                summarize(history, archive, sid, transcript_id=tid)
    digest, created = archive.project_digests.begin(pid, SummaryRequest(), "fixture-model")
    assert created
    cited = "Chronology " + " ".join(ref["marker"] for ref in digest["snapshot"]["citations"])
    archive.project_digests.finish(digest["digest_id"], content=cited)
    archive.project_digests.review(
        pid, digest["digest_id"], SummaryReviewRequest(content=cited, approved=True)
    )
    return history, projects, archive, pid


def export(archive, pid):
    op = ProjectPackageOperation(archive.packages, "export", 0, pid)
    snapshot = archive.packages.snapshot(pid, op.stop)
    op.call(op.export, snapshot)
    raw = op.zip_path.read_bytes()
    op.abort()
    return raw, snapshot[0]


def empty(tmp_path):
    history, projects, archive, pid = seed(tmp_path, count=0)
    projects.delete(pid)
    ProjectGlossaryRepository(str(history.database_path)).initialize()
    return history, projects, archive


def restore(archive, raw):
    op = ProjectPackageOperation(archive.packages, "import", 0)
    op.call(op.open_upload)
    for offset in range(0, len(raw), 511):
        op.call(op.write, raw[offset : offset + 511])
    try:
        return op.call(op.restore)
    finally:
        op.abort()


def rewrite(raw, *, graph_change=None, entry_change=None, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        entries = {info.filename: source.read(info) for info in source.infolist()}
    if graph_change:
        graph = parse_json(entries["graph.json"])
        graph_change(graph)
        entries["graph.json"] = canonical(graph).encode()
        manifest = parse_json(entries["manifest.json"])
        manifest["graph_size_bytes"] = len(entries["graph.json"])
        manifest["graph_sha256"] = hashlib.sha256(entries["graph.json"]).hexdigest()
        manifest["counts"] = {k: len(v) for k, v in graph["tables"].items()}
        entries["manifest.json"] = canonical(manifest).encode()
    if entry_change:
        entry_change(entries)
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=compression) as output:
        for name, data in entries.items():
            output.writestr(name, data)
    return result.getvalue()


def test_three_languages_multiple_recordings_all_graph_exact_roundtrip(tmp_path):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, original = export(source, pid)
    history, projects, target = empty(tmp_path / "target")
    result = restore(target, raw)
    assert result["project_id"] == pid and result["sessions"] == 3 and result["recordings"] == 4
    actual = target.packages.snapshot(pid, threading.Event())[0]
    assert canonical(actual).encode() == canonical(original).encode()
    assert {row.source_language for row in history.list_sessions(project_id=pid)} == {
        "ko",
        "en",
        "ja",
    }
    with source._connect() as conn, target._connect() as other:
        expected = conn.execute("SELECT snapshot_json,input_sha256 FROM project_digests").fetchall()
        obtained = other.execute(
            "SELECT snapshot_json,input_sha256 FROM project_digests"
        ).fetchall()
        assert [tuple(r) for r in expected] == [tuple(r) for r in obtained]
        assert other.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            other.execute("SELECT COUNT(*) FROM recording_journal WHERE registered=1").fetchone()[0]
            == 4
        )
    for row in target.recordings("session-0"):
        path = target.recording_path("session-0", row["recording_id"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
    assert target.packages.receipts()[0]["project_id"] == pid
    assert not list(target.packages.root.iterdir())


def test_collision_is_409_and_does_not_modify_either_project_or_files(tmp_path):
    _, _, source, pid = prepared(tmp_path)
    raw, original = export(source, pid)
    before = {p.name: p.read_bytes() for p in source.audio_root.iterdir()}
    with pytest.raises(PackageError) as exc:
        restore(source, raw)
    assert exc.value.status == 409
    assert source.packages.snapshot(pid, threading.Event())[0] == original
    assert {p.name: p.read_bytes() for p in source.audio_root.iterdir()} == before


def test_replacement_approved_version_preserves_stale_original_history(tmp_path):
    _, _, source, pid = prepared(tmp_path / "source")
    original = source.project_digests.listing(pid)["versions"][0]
    rec = source.recordings("session-0")[0]
    version = source.transcripts.begin(
        "session-0",
        rec,
        TranscriptRequest(recording_id=rec["recording_id"]),
    )
    segments = [
        {
            "segment_id": "replacement-id",
            "start_ms": 0,
            "end_ms": 1000,
            "text": "Later separately approved source",
        }
    ]
    source.transcripts.finish(version["transcript_id"], segments=segments)
    source.transcripts.review(
        "session-0",
        version["transcript_id"],
        TranscriptReviewRequest(
            segments=segments,
            approved=True,
        ),
    )
    assert source.project_digests.get(pid, original["digest_id"])["stale"]
    raw, graph = export(source, pid)
    _, _, target = empty(tmp_path / "target")
    restore(target, raw)
    assert target.packages.snapshot(pid, threading.Event())[0] == graph
    restored = target.project_digests.get(pid, original["digest_id"])
    assert restored["stale"] and restored["review"]["approved"]
    assert target.transcripts.approved_id("session-0") == version["transcript_id"]


@pytest.mark.parametrize(
    "kind",
    [
        "compressed",
        "traversal",
        "missing",
        "hash",
        "foreign",
        "duplicate-id",
        "unknown-table",
        "running",
        "row-limit",
        "symlink",
    ],
)
def test_hostile_packages_fail_before_any_graph_or_media_commit(tmp_path, kind):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, _ = export(source, pid)
    if kind == "compressed":
        raw = rewrite(raw, compression=zipfile.ZIP_DEFLATED)
    elif kind in {"traversal", "missing", "hash"}:

        def change(entries):
            media = next(k for k in entries if k.startswith("media/"))
            if kind == "traversal":
                entries["../outside.wav"] = entries.pop(media)
            elif kind == "missing":
                del entries[media]
            else:
                entries[media] = entries[media][:-1] + b"x"

        raw = rewrite(raw, entry_change=change)
    elif kind == "symlink":
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source_zip, zipfile.ZipFile(result, "w") as dest:
            for info in source_zip.infolist():
                if info.filename.startswith("media/"):
                    info.external_attr = 0o120777 << 16
                dest.writestr(info, source_zip.read(info))
        raw = result.getvalue()
    else:

        def change(graph):
            table = graph["tables"]
            if kind == "foreign":
                table["project_digest_sessions"][0]["session_id"] = "foreign-id"
            elif kind == "duplicate-id":
                table["sessions"].append(dict(table["sessions"][0]))
            elif kind == "unknown-table":
                table["settings"] = []
            elif kind == "running":
                table["sessions"][0]["ended_at"] = None
            else:
                table["project_digest_reviews"] *= 50001

        raw = rewrite(raw, graph_change=change)
    _, projects, target = empty(tmp_path / "target")
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        restore(target, raw)
    assert projects.list() == []
    assert list(target.audio_root.iterdir()) == []
    assert list(target.packages.root.iterdir()) == []


def test_renamed_then_rolled_back_import_is_recovered_idempotently(tmp_path, monkeypatch):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, _ = export(source, pid)
    _, projects, target = empty(tmp_path / "target")
    op = ProjectPackageOperation(target.packages, "import", 0)
    op.call(op.open_upload)
    op.call(op.write, raw)
    original = op._promote
    calls = 0

    def crash(row, identity):
        nonlocal calls
        original(row, identity)
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("owned injected SQL failure")

    monkeypatch.setattr(op, "_promote", crash)
    # Simulate process death: suppress live cleanup, then create a fresh store.
    monkeypatch.setattr(op, "_abort_locked", lambda: None)
    with pytest.raises(sqlite3.OperationalError):
        op.call(op.restore)
    op.stream.close()
    target.packages.active.clear()
    assert projects.list() == [] and list(target.audio_root.iterdir())
    assert target.packages.recover_one(threading.Event()) == 1
    assert target.packages.recover_one(threading.Event()) == 0
    assert list(target.audio_root.iterdir()) == [] and list(target.packages.root.iterdir()) == []


def test_committed_receipt_preserves_recordings_after_lost_ack_and_restart(tmp_path, monkeypatch):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, _ = export(source, pid)
    _, projects, target = empty(tmp_path / "target")
    op = ProjectPackageOperation(target.packages, "import", 0)
    op.call(op.open_upload)
    op.call(op.write, raw)
    op.call(op.restore)
    op.stream.close()
    target.packages.active.clear()
    assert target.packages.recover_one(threading.Event()) == 1
    assert projects.get(pid) and len(list(target.audio_root.iterdir())) == 4
    assert target.packages.receipts()[0]["project_id"] == pid
    assert not list(target.packages.root.iterdir())


@pytest.mark.parametrize("boundary", ["after-rename", "after-commit"])
def test_actual_process_kill_then_restart_recovers_owned_files(tmp_path, boundary):
    _, _, source, pid = prepared(tmp_path / "source")
    raw, graph = export(source, pid)
    _, projects, target = empty(tmp_path / "target")
    package = tmp_path / "owned-fixture.zip"
    package.write_bytes(raw)
    checkpoint = tmp_path / "native-checkpoint"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    child = subprocess.Popen(
        [
            sys.executable,
            __file__,
            str(target.database_path),
            str(package),
            str(checkpoint),
            boundary,
        ],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not checkpoint.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert checkpoint.exists(), child.communicate(timeout=1)
        child.kill()  # SIGKILL / TerminateProcess: no application cleanup/finalizers.
        child.wait(timeout=5)
        assert child.returncode != 0
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
    restarted = PostEventArchive(str(target.database_path))
    restarted.initialize()
    assert restarted.packages.recover_one(threading.Event()) == 1
    assert restarted.packages.recover_one(threading.Event()) == 0
    assert not list(restarted.packages.root.iterdir())
    if boundary == "after-rename":
        assert projects.get(pid) is None and not list(restarted.audio_root.iterdir())
    else:
        assert restarted.packages.snapshot(pid, threading.Event())[0] == graph
        assert restarted.packages.receipts()[0]["project_id"] == pid
        assert len(list(restarted.audio_root.iterdir())) == 4


def test_unknown_or_changed_assets_are_never_deleted_by_recovery(tmp_path):
    _, _, source, pid = prepared(tmp_path)
    op = ProjectPackageOperation(source.packages, "export", 0, pid)
    op.call(op.export, source.packages.snapshot(pid, op.stop))
    source.packages.active.clear()
    op.zip_path.write_bytes(b"changed owned asset")
    unknown = source.audio_root / (uuid4().hex + ".wav")
    unknown.write_bytes(b"unknown")
    assert source.packages.recover_one(threading.Event()) == 1
    assert op.zip_path.read_bytes() == b"changed owned asset" and unknown.read_bytes() == b"unknown"
    assert source.packages.state(op.identifier) == "quarantined"
    assert source.packages.repairs()[0]["owner_id"] == op.identifier
    assert set(source.packages.repairs()[0]) == {"owner_id", "created_at", "error"}


def test_cancel_during_native_open_closes_late_file_and_fd(tmp_path, monkeypatch):
    _, _, target = empty(tmp_path)
    op = ProjectPackageOperation(target.packages, "import", 0)
    opened, release = threading.Event(), threading.Event()
    original = op._new_file
    handles = []

    def slow(path):
        result = original(path)
        handles.append(result)
        opened.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(op, "_new_file", slow)
    failures = []

    def run():
        try:
            op.call(op.open_upload)
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert opened.wait(5)
    op.stop.set()
    release.set()
    thread.join(5)
    assert not thread.is_alive() and failures and handles[0].closed
    assert list(target.packages.root.iterdir()) == []


def test_source_missing_symlink_or_moved_digest_fail_whole_backup(tmp_path):
    _, projects, archive, pid = prepared(tmp_path)
    row = archive.recordings("session-0")[0]
    path = archive.recording_path("session-0", row["recording_id"])
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(PackageError):
        export(archive, pid)
    elsewhere = tmp_path / "unknown.wav"
    elsewhere.write_bytes(original)
    path.symlink_to(elsewhere)
    with pytest.raises((ValueError, OSError)):
        export(archive, pid)
    assert elsewhere.read_bytes() == original
    path.unlink()
    path.write_bytes(original)
    projects.assign("session-0", None)
    with pytest.raises(PackageError, match="absent session|outside this package"):
        archive.packages.snapshot(pid, threading.Event())


def _crash_checkpoint_child():
    db, package, checkpoint, boundary = sys.argv[1:]
    archive = PostEventArchive(db)
    operation = ProjectPackageOperation(archive.packages, "import", 0)
    operation.call(operation.open_upload)
    operation.call(operation.write, Path(package).read_bytes())
    original = operation._promote if boundary == "after-rename" else operation._commit
    calls = 0

    def hold(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if boundary == "after-commit" or calls == 2:
            Path(checkpoint).write_text("owned native I/O checkpoint", encoding="utf-8")
            threading.Event().wait(30)
            raise RuntimeError("Parent failed to terminate its owned child")
        return result

    if boundary == "after-rename":
        operation._promote = hold
    else:
        operation._commit = hold
    operation.call(operation.restore)


if __name__ == "__main__":
    _crash_checkpoint_child()
