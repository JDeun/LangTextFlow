from __future__ import annotations

from copy import deepcopy

import pytest

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
def test_warning_in_any_supported_target_is_rejected(target: str) -> None:
    trees = dict.fromkeys(TARGETS, "tauri v2.12.2\n")
    trees[target] += "glib v0.18.5\n"
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
