from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from langtextflow import desktop_server
from langtextflow import main as main_module


def _web_dist(tmp_path: Path) -> Path:
    root = tmp_path / "web-dist"
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text("<html>audience</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return root


def test_packaged_desktop_serves_audience_display_and_assets(tmp_path: Path) -> None:
    web_root = _web_dist(tmp_path)
    desktop_server._install_public_routes(web_root)
    client = TestClient(main_module.app, client=("192.168.1.20", 50000))

    audience = client.get("/audience/ABC234")
    display = client.get("/display/ABC234?mode=projector&lang=en")
    asset = client.get("/assets/app.js")

    assert audience.status_code == 200
    assert "audience" in audience.text
    assert display.status_code == 200
    assert asset.status_code == 200
    assert "console.log" in asset.text


def test_packaged_desktop_does_not_publish_operator_root(tmp_path: Path) -> None:
    web_root = _web_dist(tmp_path)
    desktop_server._install_public_routes(web_root)
    client = TestClient(main_module.app, client=("192.168.1.21", 50000))

    assert client.get("/").status_code == 404
    assert client.get("/api/v1/state").status_code == 403


def test_packaged_asset_route_rejects_path_escape(tmp_path: Path) -> None:
    web_root = _web_dist(tmp_path)
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    desktop_server._install_public_routes(web_root)
    client = TestClient(main_module.app, client=("192.168.1.22", 50000))

    response = client.get("/assets/%2e%2e/%2e%2e/secret.txt")
    assert response.status_code == 404
    assert "secret" not in response.text
