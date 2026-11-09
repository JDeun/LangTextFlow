"""Smoke-driver contracts only: no frozen process, native model or build is run."""
from __future__ import annotations

import json
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
    owned = smoke.Owned(SimpleNamespace(poll=lambda: 0), object())
    with pytest.raises(smoke.SmokeFailure, match="port_not_owned"):
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


def test_failure_replaces_old_success_receipt_and_does_not_hide_limits(tmp_path, monkeypatch):
    binary = tmp_path / "fixture-binary"
    binary.write_bytes(b"not executed")
    output = tmp_path / "receipt.json"
    output.write_text('{"status":"passed","stale":true}')
    monkeypatch.setattr(smoke, "process_api", lambda: object())

    def reject(*args):
        raise smoke.SmokeFailure("worker_identity_mismatch")

    monkeypatch.setattr(smoke, "worker_probe", reject)
    result = smoke.main(binary, output)
    assert result["status"] == "failed" and result["stage"] == "correction"
    assert result["error_category"] == "worker_identity_mismatch"
    assert result["valid_models_supplied"] is result["model_quality_test"] is False
    assert "stale" not in json.loads(output.read_text())
