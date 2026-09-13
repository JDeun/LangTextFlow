from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from langtextflow import desktop_server
from langtextflow import main as main_module


def test_packaged_desktop_public_surface_and_security_boundary(tmp_path: Path) -> None:
    web_root = tmp_path / "web-dist"
    assets = web_root / "assets"
    assets.mkdir(parents=True)
    (web_root / "index.html").write_text("<html>audience</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    desktop_server._install_public_routes(web_root)
    client = TestClient(main_module.app, client=("192.168.1.20", 50000))

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
