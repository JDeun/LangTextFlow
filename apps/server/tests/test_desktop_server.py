from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from langtextflow import desktop_server
from langtextflow import main as main_module
from langtextflow.desktop_shutdown import (
    desktop_shutdown_requested,
    reset_desktop_shutdown,
)


@pytest.mark.parametrize("argv", [
    ["backend"],
    ["backend", "--m2m100-worker"],
    ["backend", "--m2m100-prepare-worker"],
    ["backend", "--post-event-transcript-worker", "audio.wav"],
    ["backend", "--faster-whisper-prefetch-worker", "small", "cache"],
    ["backend", "-B", "-S", "-I", "-c",
     "from multiprocessing.resource_tracker import main;main(0)"],
])
def test_freeze_support_diverts_before_worker_or_web_setup(monkeypatch, argv):
    from langtextflow import post_event_transcript_worker

    def diverted():
        raise SystemExit(0)

    def unexpected(*args):
        pytest.fail("application setup ran before multiprocessing diversion")

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(desktop_server.multiprocessing, "freeze_support", diverted)
    monkeypatch.setattr(desktop_server, "_prepare_desktop_environment", unexpected)
    monkeypatch.setattr(post_event_transcript_worker, "main", unexpected)
    with pytest.raises(SystemExit) as stopped:
        desktop_server.main()
    assert stopped.value.code == 0


def test_packaged_desktop_public_surface_and_security_boundary(tmp_path: Path) -> None:
    web_root = tmp_path / "web-dist"
    assets = web_root / "assets"
    assets.mkdir(parents=True)
    (web_root / "index.html").write_text("<html>audience</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    reset_desktop_shutdown()
    desktop_server._install_public_routes(web_root)
    desktop_app = desktop_server.DesktopBoundaryApp(main_module.app)
    client = TestClient(desktop_app, client=("192.168.1.20", 50000))

    audience = client.get("/audience/ABC234")
    display = client.get("/display/ABC234?mode=projector&lang=en")
    asset = client.get("/assets/app.js")
    escaped = client.get("/assets/%2e%2e/%2e%2e/secret.txt")

    assert audience.status_code == 200
    assert "audience" in audience.text
    assert display.status_code == 200
    assert asset.status_code == 200
    assert "console.log" in asset.text
    assert escaped.status_code == 404
    assert "secret" not in escaped.text

    # The LAN listener publishes only audience/display assets; operator APIs remain local-only.
    assert client.get("/").status_code == 404
    assert client.get("/api/v1/state").status_code == 403
    assert client.post("/api/v1/desktop/shutdown").status_code == 403
    assert desktop_shutdown_requested() is False
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    # Developer introspection and graceful shutdown remain local-only.
    local = TestClient(desktop_app, client=("127.0.0.1", 50000))
    assert local.get("/docs").status_code == 200
    assert local.get("/openapi.json").status_code == 200
    shutdown = local.post("/api/v1/desktop/shutdown")
    assert shutdown.status_code == 202
    assert shutdown.json() == {"status": "shutting-down"}
    assert desktop_shutdown_requested() is True
    reset_desktop_shutdown()


@pytest.mark.parametrize("flag", ["--m2m100-worker", "--m2m100-prepare-worker"])
def test_m2m100_worker_dispatch_never_initializes_web(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", ["backend", flag])
    monkeypatch.setattr(desktop_server.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(desktop_server, "_prepare_desktop_environment",
                        lambda: pytest.fail("worker entered web initialization"))
    with pytest.raises(SystemExit) as stopped:
        desktop_server.main()
    assert stopped.value.code == 2
