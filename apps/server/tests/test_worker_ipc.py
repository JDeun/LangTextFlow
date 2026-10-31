import json
import subprocess
import sys

from langtextflow.worker_ipc import handle_request


def request():
    return {"protocol_version": 1, "request_id": "r1", "session_id": "s1", "session_generation": 2, "segment_id": "seg1", "source_revision": 3, "task_kind": "translation", "source_language": "ko", "target_language": "en", "deadline_ms": 5000, "payload": "안녕하세요"}


def test_worker_preserves_identity_and_unicode():
    reply = handle_request(request())
    assert reply["status"] == "ok"
    assert reply["result"] == "안녕하세요"
    assert reply["source_revision"] == 3


def test_worker_rejects_unsupported_protocol():
    data = request()
    data["protocol_version"] = 2
    assert handle_request(data)["error"] == "unsupported_protocol_version"


def test_jsonl_subprocess_roundtrip():
    proc = subprocess.run([sys.executable, "-m", "langtextflow.worker_ipc"], input=json.dumps(request(), ensure_ascii=False) + "\n", text=True, capture_output=True, timeout=5, check=True)
    assert json.loads(proc.stdout)["result"] == "안녕하세요"
    assert proc.stderr == ""
