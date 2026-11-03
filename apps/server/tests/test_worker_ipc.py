import json
import subprocess
import sys

import pytest

from langtextflow.worker_ipc import MAX_FRAME_BYTES, handle_request


def request():
    return {
        "protocol_version": 1, "request_id": "r1", "session_id": "s1",
        "session_generation": 2, "segment_id": "seg1", "source_revision": 3,
        "task_kind": "translation", "source_language": "ko", "target_language": "en",
        "deadline_ms": 5000, "payload": "안녕하세요",
    }


def run_worker(data: bytes):
    return subprocess.run(
        [sys.executable, "-m", "langtextflow.worker_ipc"], input=data,
        capture_output=True, timeout=5, check=True,
    )


def test_worker_preserves_identity_and_unicode():
    reply = handle_request(request())
    assert reply["status"] == "ok"
    assert reply["result"] == "안녕하세요"
    assert reply["source_revision"] == 3


@pytest.mark.parametrize(("field", "value", "error"), [
    ("protocol_version", 2, "unsupported_protocol_version"),
    ("task_kind", "shell", "unsupported_task_kind"),
    ("payload", {"command": "run"}, "invalid_payload"),
])
def test_worker_rejects_unsupported_requests(field, value, error):
    assert handle_request({**request(), field: value})["error"] == error


def test_jsonl_subprocess_roundtrip_never_executes_payload():
    payload = "__import__('os')._exit(99)\n한글"
    data = {**request(), "payload": payload}
    proc = run_worker((json.dumps(data, ensure_ascii=False) + "\n").encode())
    assert json.loads(proc.stdout)["result"] == payload
    assert proc.stderr == b""


def test_malformed_frame_does_not_poison_next_request():
    data = b"not-json\n[]\n" + (json.dumps(request()) + "\n").encode()
    replies = [json.loads(line) for line in run_worker(data).stdout.splitlines()]
    assert [reply["status"] for reply in replies] == ["error", "error", "ok"]
    assert replies[-1]["request_id"] == "r1"


def test_invalid_utf8_or_surrogate_does_not_poison_next_request():
    invalid = json.dumps({**request(), "payload": "\ud800"}).encode() + b"\n"
    data = b"\xff\n" + invalid + (json.dumps(request()) + "\n").encode()
    replies = [json.loads(line) for line in run_worker(data).stdout.splitlines()]
    assert [reply["status"] for reply in replies] == ["error", "error", "ok"]
    assert replies[1]["error"] == "invalid_utf8"


def test_oversized_frame_terminates_without_processing_following_request():
    data = b"x" * MAX_FRAME_BYTES + b"\n" + (json.dumps(request()) + "\n").encode()
    proc = run_worker(data)
    replies = proc.stdout.splitlines()
    assert len(replies) == 1
    assert json.loads(replies[0])["error"] == "frame_too_large_or_unterminated"
    assert len(proc.stdout) < MAX_FRAME_BYTES


def test_parent_can_terminate_idle_fixture_and_restart_with_new_generation():
    proc = subprocess.Popen(
        [sys.executable, "-m", "langtextflow.worker_ipc"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        proc.terminate()
        proc.communicate(timeout=5)
        assert proc.returncode is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
    next_request = {**request(), "session_generation": 3}
    reply = json.loads(run_worker((json.dumps(next_request) + "\n").encode()).stdout)
    assert reply["status"] == "ok"
    assert reply["session_generation"] == 3
