"""Prevent a regression to known-vulnerable versions in the locked dev install."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("name", "minimum"),
    [
        ("pypdf", (6, 19, 0)),
        ("urllib3", (2, 8, 0)),
    ],
)
def test_patched_dependency_floor_in_uv_lock(name: str, minimum: tuple[int, ...]) -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = [package for package in lock["package"] if package["name"] == name]
    assert len(packages) == 1
    package = packages[0]
    version = tuple(int(part) for part in package["version"].split(".")[:3])
    assert version >= minimum, f"{name} lockfile version reintroduced a known advisory"
    assert package["wheels"]
    assert package["sdist"]["hash"].startswith("sha256:")
    for item in [package["sdist"], *package["wheels"]]:
        assert item["url"].startswith("https://files.pythonhosted.org/packages/")
        assert len(item["hash"].removeprefix("sha256:")) == 64


def test_direct_pypdf_constraint_also_excludes_vulnerable_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(
        requirement.startswith("pypdf>=6.19")
        for requirement in project["project"]["dependencies"]
    )
