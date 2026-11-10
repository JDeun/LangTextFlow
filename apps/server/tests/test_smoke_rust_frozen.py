"""Smoke-driver contracts only: no frozen process, native model or build is run."""
from __future__ import annotations

import json
import sqlite3
from io import StringIO
from types import SimpleNamespace

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
