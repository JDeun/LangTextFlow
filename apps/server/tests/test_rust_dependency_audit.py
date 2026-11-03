from __future__ import annotations

from copy import deepcopy

import pytest

from scripts import audit_rust_dependencies as audit_module
from scripts.audit_rust_dependencies import TARGETS, qualify


def report() -> dict:
    return {"settings": {"ignore": [], "target_arch": [], "target_os": [], "severity": None,
                         "informational_warnings": ["unmaintained", "unsound", "notice"]},
            "vulnerabilities": {"found": False, "count": 0, "list": []},
            "warnings": {"unsound": [{"advisory": {"id": "RUSTSEC-2024-0429"},
                         "package": {"name": "glib", "version": "0.18.5"}}]}}


def test_known_warning_requires_all_supported_target_graphs() -> None:
    trees = dict.fromkeys(TARGETS, "tauri v2.12.2\n")
    result = qualify(report(), trees, desktop=True)
    assert result["passed"] and len(result["target_exclusions"]) == 1
    assert result["linux_desktop_qualified"] is False
    assert not qualify(report(), {}, desktop=True)["passed"]
    assert not qualify(report(), trees, desktop=False)["passed"]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("suffix", ["", " (/owned/path-fixture)"])
def test_warning_in_any_supported_target_is_rejected(target: str, suffix: str) -> None:
    trees = dict.fromkeys(TARGETS, "tauri v2.12.2\n")
    trees[target] += f"glib v0.18.5{suffix}\n"
    assert not qualify(report(), trees, desktop=True)["passed"]


def test_unknown_warning_or_vulnerability_cannot_be_excluded() -> None:
    trees = dict.fromkeys(TARGETS, "")
    unknown = deepcopy(report())
    unknown["warnings"]["unsound"][0]["advisory"]["id"] = "RUSTSEC-2099-0001"
    assert not qualify(unknown, trees, desktop=True)["passed"]
    vulnerable = report()
    vulnerable["vulnerabilities"] = {"found": True, "count": 1, "list": [{}]}
    assert not qualify(vulnerable, trees, desktop=True)["passed"]


@pytest.mark.parametrize("setting,value", [("ignore", ["RUSTSEC-2026-0429"]),
                                         ("target_os", ["windows"]),
                                         ("target_arch", ["x86_64"]),
                                         ("severity", "high"),
                                         ("informational_warnings", [])])
def test_ambient_audit_config_cannot_hide_findings(setting: str, value: object) -> None:
    filtered = report()
    filtered["settings"][setting] = value
    assert not qualify(filtered, dict.fromkeys(TARGETS, ""), desktop=True)["passed"]


def test_yanked_warning_without_advisory_is_explicit_failure() -> None:
    yanked = report()
    yanked["warnings"] = {"yanked": [{"advisory": None,
                                   "package": {"name": "fixture", "version": "1.0.0"}}]}
    result = qualify(yanked, dict.fromkeys(TARGETS, ""), desktop=True)
    assert result["errors"] == ["unqualified_warning:yanked"]


@pytest.mark.parametrize("failure", [None, "json_registry", "hidden_index", "mutation",
                                    "quiet_terminal", "unknown_terminal"])
def test_gate_controlled_config_registry_completeness_and_lock_fences(
    tmp_path, monkeypatch, failure,
) -> None:
    import json
    import subprocess
    import sys

    monkeypatch.setattr(audit_module, "ROOT", tmp_path)
    for path in audit_module.MANIFESTS.values():
        lock = tmp_path / path
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.with_name("Cargo.lock").write_text("fixed-lock")
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo/audit.toml").write_text("[yanked]\nenabled=false\n")
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["audit", "--database", str(tmp_path),
                                     "--output", str(output)])
    prepared = set()

    def fake_run(command, *, cwd=None):
        stdout, stderr = "", ""
        if "--version" in command:
            stdout = "cargo-audit 0.22.2\n"
        elif command[0] == "git":
            stdout = "0" * 40
        elif "fetch" in command:
            prepared.add(command[-1])
        elif "audit" in command:
            assert (cwd / ".cargo/audit.toml").read_text() == audit_module.AUDIT_CONFIG
            assert cwd != tmp_path  # Poisoned project/home configuration is not consulted.
            lock = command[command.index("--file") + 1]
            assert str(audit_module.Path(lock).with_name("Cargo.toml")) in prepared
            if "--json" in command:
                clean = report()
                clean["warnings"] = {}
                stdout = json.dumps(clean)
                if failure == "json_registry":
                    stderr = "error: couldn't check if the package is yanked: missing crate\n"
                if failure == "mutation":
                    audit_module.Path(lock).write_text("changed-lock")
            else:
                stderr = ("Loaded 1403 security advisories (from fixture)\n"
                          "Scanning fixture for vulnerabilities (56 crate dependencies)\n")
                if failure == "hidden_index":
                    stderr += "warning: couldn't open crates.io index: missing cache\n"
                elif failure == "unknown_terminal":
                    stderr += "unrecognized diagnostic\n"
                elif failure == "quiet_terminal":
                    stderr = ""
        return subprocess.CompletedProcess(command, 0, stdout, stderr)

    monkeypatch.setattr(audit_module, "run", fake_run)
    assert audit_module.main() == (0 if failure is None else 1)
    result = json.loads((output / "qualification.json").read_text())
    if failure:
        assert result["passed"] is False
        assert result["error"]
    else:
        assert all(item["passed"] for item in result["locks"].values())
