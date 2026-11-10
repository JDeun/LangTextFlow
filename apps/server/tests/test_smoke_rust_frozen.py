"""Smoke-driver contracts only: no frozen process, native model or build is run."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import closing
from io import BytesIO, StringIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import smoke_rust_frozen as smoke


def wire(request, result="성령 🙏 日本語", error=None, **changes):
    reply = {key: request[key] for key in smoke.IDENTITY}
    reply.update(status="error" if error else "ok", result=None if error else result, error=error)
    reply.update(changes)
    return (json.dumps(reply, ensure_ascii=False) + "\n").encode()


def test_utf8_and_fixed_identity_are_validated_without_locale_dependency():
    request = smoke.request_frame("correction", {"text": "성령 🙏 日本語"})
    assert smoke.validate_reply(wire(request), request, "성령 🙏 日本語", None)["result"]
    assert request["target_language"] is None
    with pytest.raises(smoke.SmokeFailure, match="identity"):
        smoke.validate_reply(wire(request, request_id="other"), request, "성령 🙏 日本語", None)
    with pytest.raises(smoke.SmokeFailure, match="identity"):
        smoke.validate_reply(wire(request, protocol_version=True), request, "성령 🙏 日本語", None)


@pytest.mark.parametrize("task", ["asr", "translation"])
def test_only_generic_failure_with_no_result_is_accepted(task):
    request = smoke.request_frame(task, {})
    code = task + "_worker_failed"
    assert smoke.validate_reply(wire(request, error=code), request, None, code)["error"] == code
    with pytest.raises(smoke.SmokeFailure, match="result"):
        smoke.validate_reply(wire(request, error="private path or unbounded error"),
                             request, None, code)
    with pytest.raises(smoke.SmokeFailure, match="fields"):
        smoke.validate_reply(wire(request, error=code, private_path="leak"), request, None, code)
    with pytest.raises(smoke.SmokeFailure, match="frame_size"):
        smoke.validate_reply(b"x" * (smoke.FRAME_LIMIT + 1), request, None, code)


def processes():
    return [
        {"pid": 10, "created": 1., "ppid": 1,
         "exe": "/owned/_MEIfixture/runtime/langtextflow-runtime", "args": ["runtime"]},
        {"pid": 11, "created": 2., "ppid": 10,
         "exe": "/owned/server", "args": ["server", "--asr-inference-worker"]},
    ]


def test_direct_rust_child_rejects_inner_bootloader_and_unbundled_helper():
    rows = processes()
    runtime, worker = smoke.direct_asr(rows)
    assert worker["ppid"] == runtime["pid"]
    with pytest.raises(smoke.SmokeFailure, match="inner"):
        smoke.direct_asr(rows + [{**rows[1], "pid": 12, "ppid": 11}])
    with pytest.raises(smoke.SmokeFailure, match="direct"):
        smoke.direct_asr([rows[0], {**rows[1], "ppid": 8}])
    with pytest.raises(smoke.SmokeFailure, match="bundle"):
        smoke.direct_asr([{**rows[0], "exe": "/external/langtextflow-runtime"}, rows[1]])


@pytest.mark.parametrize("valid", [True, False])
def test_process_graph_is_preserved_before_assertion_without_private_fields(valid):
    rows = processes()
    rows[1]["args"] += ["--rust-protocol", "/private/model", "token-secret"]
    if not valid:
        rows.append({**rows[1], "pid": 12, "ppid": 11})
    owned = SimpleNamespace(snapshot=lambda: rows)
    log = StringIO()
    if valid:
        smoke.recorded_direct_asr(owned, log)
    else:
        with pytest.raises(smoke.SmokeFailure, match="inner"):
            smoke.recorded_direct_asr(owned, log)
    graph = json.loads(log.getvalue())["processes"]
    assert len(graph) == len(rows)
    assert all(set(row) == {"pid", "ppid", "created", "exe_basename",
                            "fixed_worker_flags", "runtime_from_bundle"} for row in graph)
    assert graph[0]["runtime_from_bundle"] is True
    assert graph[1]["runtime_from_bundle"] is False
    assert graph[1]["fixed_worker_flags"] == ["--asr-inference-worker", "--rust-protocol"]
    assert "/owned" not in log.getvalue() and "/private" not in log.getvalue()
    assert "token-secret" not in log.getvalue() and "args" not in log.getvalue()


class FakeError(Exception):
    pass


class FakeMissing(FakeError):
    pass


def test_process_discovery_denied_is_failure_instead_of_empty_success():
    def denied(**kwargs):
        raise PermissionError("sysctl denied")

    node = SimpleNamespace(children=denied)
    api = SimpleNamespace(Process=lambda pid: node, Error=FakeError, NoSuchProcess=FakeMissing)
    owned = smoke.Owned(SimpleNamespace(pid=10, poll=lambda: None), api)
    with pytest.raises(smoke.SmokeFailure, match="observation_unavailable"):
        owned.snapshot()
    assert owned.observations == 0
    with pytest.raises(smoke.SmokeFailure, match="empty_ownership"):
        owned.gone([])


@pytest.mark.parametrize("race_at", ["lookup", "children", "fields"])
def test_previously_observed_parent_exit_race_requires_authoritative_terminal_poll(race_at):
    state = {"race": False, "exit": None}

    def disappear():
        state["exit"] = 1
        raise FakeMissing()

    def lookup(pid):
        if state["race"] and race_at == "lookup":
            disappear()
        return node

    def children(**kwargs):
        if state["race"] and race_at == "children":
            disappear()
        return []

    def created():
        if state["race"] and race_at == "fields":
            disappear()
        return 1.

    node = SimpleNamespace(pid=10, children=children, create_time=created,
                           ppid=lambda: 1, exe=lambda: "owned", cmdline=lambda: ["owned"])
    api = SimpleNamespace(Process=lookup, Error=FakeError, NoSuchProcess=FakeMissing)
    owned = smoke.Owned(SimpleNamespace(pid=10, poll=lambda: state["exit"]), api)
    assert owned.snapshot()[0]["pid"] == 10
    state["race"] = True
    assert owned.snapshot() == []
    assert owned.observations == 1 and owned.seen[10]["created"] == 1.


@pytest.mark.parametrize("observed,terminal,error,category", [
    (True, False, FakeMissing, "parent_unobserved"),
    (False, True, FakeMissing, "parent_unobserved"),
    (True, True, PermissionError, "observation_unavailable"),
])
def test_missing_or_denied_parent_is_not_an_exit_proof(observed, terminal, error, category):
    state = {"exit": None}

    def failed_lookup(pid):
        state["exit"] = 1 if terminal else None
        raise error()

    api = SimpleNamespace(Process=failed_lookup, Error=FakeError, NoSuchProcess=FakeMissing)
    owned = smoke.Owned(SimpleNamespace(pid=10, poll=lambda: state["exit"]), api)
    if observed:
        owned.seen[10] = {"pid": 10, "created": 1.}
        owned.observations = 1
    with pytest.raises(smoke.SmokeFailure, match=category):
        owned.snapshot()


def test_cleanup_never_kills_a_reused_pid(monkeypatch):
    kills = []
    node = SimpleNamespace(create_time=lambda: 99., is_running=lambda: True,
                           kill=lambda: kills.append("wrong process"))
    api = SimpleNamespace(Process=lambda pid: node, Error=FakeError, NoSuchProcess=FakeMissing)
    process = SimpleNamespace(pid=10, poll=lambda: 0, wait=lambda **kwargs: 0)
    owned = smoke.Owned(process, api)
    owned.seen = {10: {"pid": 10, "created": 1.}}
    monkeypatch.setattr(smoke.os, "killpg", lambda *args: kills.append("wrong group"),
                        raising=False)
    owned.cleanup()
    assert kills == []


def test_discovery_failure_still_waits_for_original_child(monkeypatch):
    actions = []

    def denied(**kwargs):
        raise PermissionError("denied")

    api = SimpleNamespace(Process=lambda pid: SimpleNamespace(children=denied),
                          Error=FakeError, NoSuchProcess=FakeMissing)
    process = SimpleNamespace(pid=10, poll=lambda: None,
                              terminate=lambda: actions.append("terminate"),
                              wait=lambda **kw: actions.append("wait"))
    owned = smoke.Owned(process, api)
    monkeypatch.setattr(smoke.os, "killpg", lambda *args: actions.append("kill_owned_group"),
                        raising=False)
    with pytest.raises(smoke.SmokeFailure, match="observation_unavailable"):
        owned.cleanup()
    assert "wait" in actions
    assert "terminate" in actions or "kill_owned_group" in actions


def test_dead_backend_cannot_authorize_post_to_a_foreign_listener():
    api = SimpleNamespace(Error=FakeError, NoSuchProcess=FakeMissing)
    owned = smoke.Owned(SimpleNamespace(pid=10, poll=lambda: 0), api)
    with pytest.raises(smoke.SmokeFailure, match="parent_unobserved"):
        owned.listener_owned(12345)


def test_environment_excludes_ambient_app_settings_and_keeps_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGTEXTFLOW_RUST_RUNTIME_BINARY", "/foreign/helper")
    monkeypatch.setenv("LANGTEXTFLOW_OLLAMA_URL", "https://unapproved.invalid")
    monkeypatch.setenv("HF_TOKEN", "fixture-secret")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "stale")
    env = smoke.environment(tmp_path)
    assert env["LANGTEXTFLOW_RUST_RUNTIME_BINARY"] == ""
    assert env["LANGTEXTFLOW_RUNTIME_CORE"] == "rust"
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    assert "HF_TOKEN" not in env and "_PYI_PARENT_PROCESS_LEVEL" not in env
    assert env["LANGTEXTFLOW_OLLAMA_URL"] == "http://127.0.0.1:1"


def test_unobservable_host_writes_failed_receipt_before_any_target_spawn(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-binary"
    binary.write_bytes(b"not executed")

    def denied():
        raise PermissionError("host process APIs unavailable")

    monkeypatch.setattr(smoke, "process_api", denied)
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    output = tmp_path / "receipt.json"
    result = smoke.main(binary, output)
    assert result["status"] == "failed"
    assert result["error_category"] == "PermissionError"
    assert result["stage"] == "preflight" and result["probes"] == []
    assert json.loads(output.read_text()) == result


def test_existing_receipt_is_preserved_before_fixture_or_process_creation(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-binary"
    binary.write_bytes(b"not executed")
    output = tmp_path / "receipt.json"
    original = '{"status":"passed","original":true}'
    output.write_text(original)
    monkeypatch.setattr(smoke, "process_api", lambda: pytest.fail("must not observe or spawn"))
    monkeypatch.setattr(smoke.tempfile, "mkdtemp",
                        lambda **kw: pytest.fail("must not create fixture"))
    with pytest.raises(smoke.SmokeFailure, match="receipt_already_exists"):
        smoke.main(binary, output)
    assert output.read_text() == original


def test_failed_fixture_keeps_private_sqlite_logs_and_success_removes_only_its_own(
    tmp_path, monkeypatch,
):
    binary = tmp_path / "fixture-binary"
    binary.write_bytes(b"not executed")
    output = tmp_path / "failed.json"
    monkeypatch.setattr(smoke, "process_api", lambda: object())
    monkeypatch.setattr(smoke.subprocess, "Popen",
                        lambda *a, **kw: pytest.fail("fake contracts only"))

    def reject(binary, root, env, api, kind):
        (root / "smoke.sqlite").write_bytes(b"owned fixture diagnostic")
        (root / "worker-correction.stderr.log").write_text("private endpoint/path detail")
        raise smoke.SmokeFailure("worker_identity_mismatch")

    monkeypatch.setattr(smoke, "worker_probe", reject)
    result = smoke.main(binary, output)
    assert result["status"] == "failed" and result["stage"] == "correction"
    assert result["error_category"] == "worker_identity_mismatch"
    assert result["valid_models_supplied"] is result["model_quality_test"] is False
    fixture = tmp_path / result["private_fixture"]
    assert result["evidence_visibility"] == "private" and result["private_fixture_retained"]
    assert fixture.parent == tmp_path and fixture.is_dir()
    assert (fixture / "smoke.sqlite").read_bytes() == b"owned fixture diagnostic"
    assert (fixture / "worker-correction.stderr.log").read_text() == "private endpoint/path detail"
    assert "endpoint/path" not in output.read_text() and str(tmp_path) not in output.read_text()

    monkeypatch.setattr(smoke, "worker_probe", lambda *args: {"status": "passed"})
    monkeypatch.setattr(smoke, "backend_probe", lambda *args: {"status": "passed"})
    passed = smoke.main(binary, tmp_path / "passed.json")
    assert passed["status"] == "passed" and not passed["private_fixture_retained"]
    assert passed["private_fixture"] != result["private_fixture"]
    assert not (tmp_path / passed["private_fixture"]).exists()
    assert fixture.is_dir()  # A later successful attempt cannot remove the failed evidence.


def test_loopback_redirects_cannot_send_smoke_requests_elsewhere():
    with pytest.raises(smoke.SmokeFailure, match="redirect_forbidden"):
        smoke.NoRedirect().redirect_request(None, None, 307, "redirect", {},
                                             "https://unapproved.invalid/receive")


@pytest.mark.parametrize("case", ["ended", "not_ended", "query_error"])
def test_session_database_handle_closes_before_fixture_cleanup(tmp_path, monkeypatch, case):
    path = tmp_path / "smoke.sqlite"
    database = sqlite3.connect(path)
    if case != "query_error":
        database.execute("CREATE TABLE sessions(session_id TEXT, ended_at TEXT)")
        database.execute("INSERT INTO sessions VALUES (?, ?)",
                         ("session", "finished" if case == "ended" else None))
        database.commit()
    # Retaining this reference prevents garbage collection from hiding a leak.
    monkeypatch.setattr(smoke.sqlite3, "connect", lambda *args, **kwargs: database)
    if case == "ended":
        assert smoke.session_ended_at(path, "session") == "finished"
    elif case == "not_ended":
        with pytest.raises(smoke.SmokeFailure, match="session_not_ended"):
            smoke.session_ended_at(path, "session")
    else:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            smoke.session_ended_at(path, "session")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        database.execute("SELECT 1")
    path.unlink()


def test_refusal_observes_direct_child_while_http_is_pending_on_owner_thread():
    release = threading.Event()
    owner_thread = threading.get_ident()
    gone = []
    snapshots = 0

    def snapshot():
        nonlocal snapshots
        assert threading.get_ident() == owner_thread
        snapshots += 1
        if snapshots == 1:
            return []
        release.set()
        return processes()

    def operation():
        assert threading.get_ident() != owner_thread
        assert release.wait(1), "HTTP must remain pending until child observation"
        return {"detail": "fixed refusal"}

    owned = SimpleNamespace(snapshot=snapshot, gone=lambda rows: gone.extend(rows),
                            cleanup=lambda: pytest.fail("success must not force cleanup"))
    log = StringIO()
    result = smoke.observed_start_refusal(owned, log, operation)
    assert result["rust_pid"] == 10 and result["rust_created"] == 1.
    assert result["asr_pid"] == 11 and result["asr_created"] == 2.
    assert result["asr_parent_pid"] == 10 and result["direct_pid_no_inner"]
    assert result["observed_children_absent"] and result["observed_children"] == 2
    assert result["unobserved_descendants_excluded"]
    assert {row["pid"] for row in gone} == {10, 11}
    assert "processes" in log.getvalue() and "/owned" not in log.getvalue()


def test_stream_dispatch_control_is_valid_but_never_opens_a_sidecar(tmp_path, monkeypatch):
    from langtextflow.asr_stream_worker import StreamLeaf, validate

    process = SimpleNamespace(
        stdin=BytesIO(), stdout=BytesIO(), returncode=1,
        poll=lambda: 1, wait=lambda **kwargs: 1,
    )
    owned = SimpleNamespace(process=process, observations=1, seen={1: {"pid": 1}},
                            snapshot=lambda: [], gone=lambda rows: None, cleanup=lambda: None)

    def spawn(arguments, **kwargs):
        assert arguments[1:] == ["--asr-stream-worker"]
        return process

    def exchange(owner, request):
        assert validate(request)["mode"] == "poll" and request["task_kind"] == "asr"
        with pytest.raises(ValueError, match="stale_stream_candidate"):
            asyncio.run(StreamLeaf().handle(request))
        return wire(request, error="asr_stream_failed")

    monkeypatch.setattr(smoke.subprocess, "Popen", spawn)
    monkeypatch.setattr(smoke, "Owned", lambda *args: owned)
    monkeypatch.setattr(smoke, "exchange", exchange)
    result = smoke.worker_probe(tmp_path / "unused", tmp_path, {}, object(), "stream")
    assert result["generic_error"] == "asr_stream_failed" and result["exit_code"] == 1


@pytest.mark.parametrize("failure", ["unobserved", "denied", "eof"])
def test_refusal_never_promotes_missing_observation_or_eof_to_cleanup_success(failure):
    actions = []
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls > 1 and failure == "denied":
            raise smoke.SmokeFailure("process_observation_unavailable")
        return []

    def operation():
        if failure == "eof":
            raise EOFError("owned fixture EOF")
        return {"detail": "fixed refusal"}

    owned = SimpleNamespace(snapshot=snapshot, gone=lambda rows: pytest.fail("no child proof"),
                            cleanup=lambda: actions.append("cleanup"))
    expected = EOFError if failure == "eof" else smoke.SmokeFailure
    with pytest.raises(expected):
        smoke.observed_start_refusal(owned, StringIO(), operation)
    assert actions == ["cleanup"]
    assert not any(thread.name == "frozen-start-request" for thread in threading.enumerate())


@pytest.mark.parametrize("status,ended", [(503, True), (200, True), (503, False)])
def test_backend_requires_two_refusals_ended_sqlite_and_same_healthy_backend(
    tmp_path, monkeypatch, status, ended,
):
    state = {"session_id": None, "running": False}
    calls, actions = [], []
    process = SimpleNamespace(pid=1, returncode=None, poll=lambda: process.returncode,
                              wait=lambda **kwargs: process.returncode)
    owned = SimpleNamespace(
        seen={1: {"pid": 1, "created": 1.}}, observations=1,
        snapshot=lambda: [], listener_owned=lambda port: actions.append("listener"),
        gone=lambda rows: actions.append("gone"), cleanup=lambda: actions.append("cleanup"),
    )
    monkeypatch.setattr(smoke.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(smoke, "Owned", lambda *args: owned)
    sock = SimpleNamespace(bind=lambda addr: None, getsockname=lambda: ("127.0.0.1", 4242))

    class Socket:
        def __enter__(self):
            return sock

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(smoke.socket, "socket", lambda: Socket())
    with closing(sqlite3.connect(tmp_path / "smoke.sqlite")) as database:
        database.execute("CREATE TABLE sessions(session_id TEXT, ended_at TEXT)")

    def open_request(request, timeout):
        path = request.full_url.split(":4242")[1]
        assert timeout == (smoke.START_REQUEST_SECONDS if path.endswith("/start") else 30)
        calls.append(path)
        value = {"status": "ok"}
        if path.endswith("/start"):
            sid = f"session-{len([p for p in calls if p.endswith('/start')])}"
            state.update(session_id=sid)
            with closing(sqlite3.connect(tmp_path / "smoke.sqlite")) as database:
                database.execute("INSERT INTO sessions VALUES (?, ?)",
                                 (sid, "finished" if ended else None))
                database.commit()
            if status == 503:
                raise HTTPError(request.full_url, 503, "refused", {},
                                BytesIO(b'{"detail":"fixed refusal"}'))
            value = {"running": True, "session_id": sid}
        elif path.endswith("/state"):
            value = state
        elif path.endswith("/shutdown"):
            process.returncode = 0
        response = BytesIO(json.dumps(value).encode())
        response.getcode = lambda: 202 if path.endswith("/shutdown") else 200
        return response

    monkeypatch.setattr(smoke, "build_opener", lambda *args: SimpleNamespace(open=open_request))

    def observed(owned, log, operation):
        operation()  # Exercise actual HTTPError/status/body handling.
        return {"direct_pid_no_inner": True, "observed_children_absent": True,
                "unobserved_descendants_excluded": True}

    monkeypatch.setattr(smoke, "observed_start_refusal", observed)
    if status != 503 or not ended:
        with pytest.raises(smoke.SmokeFailure, match="status|session_not_ended"):
            smoke.backend_probe(tmp_path / "unused", tmp_path, {}, object())
    else:
        result = smoke.backend_probe(tmp_path / "unused", tmp_path, {}, object())
        cycles = result["invalid_cache_start_refusal_retry"]
        assert [cycle["session_id"] for cycle in cycles] == ["session-1", "session-2"]
        assert all(cycle["http_status"] == 503 and cycle["ended_at"] for cycle in cycles)
        assert result["model_load_or_recognition_proven"] is False
        assert result["observed_processes_absent"] and result["unobserved_descendants_excluded"]
        assert calls.count("/api/v1/session/start") == 2
        assert calls[-1] == "/api/v1/desktop/shutdown" and process.returncode == 0
    assert actions[-1] == "cleanup"
