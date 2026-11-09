"""Packaging contract without compiling Rust or freezing a Python interpreter."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_desktop_sidecar as build


def test_cross_arch_target_refused_before_removing_existing_outputs(tmp_path, monkeypatch):
    old = tmp_path / "build/desktop-sidecar/keep"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"previous output")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").touch()
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "WEB_DIST", web)
    monkeypatch.setenv("TAURI_TARGET_TRIPLE", "x86_64-pc-windows-msvc")
    monkeypatch.setattr(build.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="rustc fixture\nhost: aarch64-apple-darwin\n",
    ))
    with pytest.raises(RuntimeError, match="native Rust host"):
        build.main()
    assert old.read_bytes() == b"previous output"


@pytest.mark.parametrize("platform,suffix", [("darwin", ""), ("win32", ".exe")])
def test_packaged_runtime_is_built_and_embedded_before_publishing_sidecar(
    tmp_path, monkeypatch, platform, suffix,
):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "BIN_DIR", tmp_path / "binaries")
    monkeypatch.setattr(build, "WEB_DIST", tmp_path / "web")
    build.WEB_DIST.mkdir()
    (build.WEB_DIST / "index.html").touch()
    monkeypatch.setattr(build.sys, "platform", platform)
    monkeypatch.setattr(build, "_target_triple", lambda: "native-host")
    monkeypatch.setattr(build, "_collect_args", lambda *args: [])
    runtime = tmp_path / f"crates/target/native-host/release/langtextflow-runtime{suffix}"
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if command[0] == "cargo":
            assert "+1.90.0" in command and "--locked" in command
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"native runtime")
        else:
            separator = ";" if platform == "win32" else ":"
            assert runtime.is_file()
            assert command[command.index("--add-binary") + 1] == f"{runtime}{separator}runtime"
            dist = Path(command[command.index("--distpath") + 1])
            (dist / f"langtextflow-server{suffix}").write_bytes(b"frozen backend")

    monkeypatch.setattr(build.subprocess, "run", execute)
    build.main()
    assert len(calls) == 2
    published = build.BIN_DIR / f"langtextflow-server-native-host{suffix}"
    assert published.read_bytes() == b"frozen backend"
