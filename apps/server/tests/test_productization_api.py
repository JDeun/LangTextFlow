from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from langtextflow import main as main_module
from langtextflow.mdns import MdnsState


def _local_client() -> TestClient:
    return TestClient(main_module.app, client=("127.0.0.1", 50000))


def test_mdns_status_is_operator_only(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module.mdns_publisher,
        "_state",
        MdnsState(hostname="caption-room.local", ready=True),
    )

    response = _local_client().get("/api/v1/network/mdns")
    assert response.status_code == 200
    assert response.json() == {
        "hostname": "caption-room.local",
        "ready": True,
        "error": None,
    }

    remote = TestClient(main_module.app, client=("192.168.1.50", 50000))
    assert remote.get("/api/v1/network/mdns").status_code == 403


def test_diagnostics_endpoint_returns_private_no_store_zip(monkeypatch) -> None:
    async def fake_preflight(*args, **kwargs):
        return {"ready": True, "details": {"path": "/home/alice/.cache/model"}}

    monkeypatch.setattr(main_module, "run_preflight", fake_preflight)
    response = _local_client().get("/api/v1/diagnostics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    assert "langtextflow-diagnostics-" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["privacy"]["contains_join_code"] is False
        assert "settings.json" in archive.namelist()
        assert "metrics.json" in archive.namelist()


def test_diagnostics_endpoint_rejects_remote_and_hostile_origin() -> None:
    remote = TestClient(main_module.app, client=("192.168.1.50", 50000))
    assert remote.get("/api/v1/diagnostics").status_code == 403

    hostile = _local_client().get(
        "/api/v1/diagnostics",
        headers={"Origin": "https://localhost.evil.example"},
    )
    assert hostile.status_code == 403
