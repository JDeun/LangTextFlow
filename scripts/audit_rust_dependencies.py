"""Audit both complete locks; qualify only evidenced Windows/macOS GTK exclusions."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("x86_64-pc-windows-msvc", "aarch64-apple-darwin", "x86_64-apple-darwin")
# These remain findings in the complete lock. Linux desktop is NOT qualified.
LINUX_WARNINGS = {
    ("RUSTSEC-2024-0429", "glib", "0.18.5", "unsound"),
    ("RUSTSEC-2024-0370", "proc-macro-error", "1.0.4", "unmaintained"),
}


def qualify(report: dict, trees: dict[str, str], *, desktop: bool) -> dict:
    """Never suppress vulnerabilities, new warnings, or a supported-target finding."""
    errors = []
    exclusions = []
    settings = report["settings"]
    if any(settings[key] for key in ("ignore", "target_arch", "target_os", "severity")):
        errors.append("filtered_or_ignored_audit_configuration")
    if set(settings["informational_warnings"]) != {"unmaintained", "unsound", "notice"}:
        errors.append("incomplete_warning_configuration")
    vulnerabilities = report["vulnerabilities"]
    if vulnerabilities["found"] or vulnerabilities["count"] or vulnerabilities["list"]:
        errors.append("vulnerabilities_in_complete_lock")
    for kind, warnings in report["warnings"].items():
        for warning in warnings:
            package = warning["package"]
            key = (warning["advisory"]["id"], package["name"], package["version"], kind)
            if not desktop or key not in LINUX_WARNINGS:
                errors.append(f"unqualified_warning:{key[0]}")
                continue
            if set(trees) != set(TARGETS):
                errors.append("missing_supported_target_graph")
                continue
            needle = f"{package['name']} v{package['version']}"
            present = [target for target, tree in trees.items() if needle in tree.splitlines()]
            if present:
                errors.append(f"supported_target_warning:{key[0]}:{','.join(present)}")
            else:
                exclusions.append({"advisory": key[0], "package": needle,
                                   "reason": "absent_from_all_supported_target_graphs"})
    return {"passed": not errors, "errors": errors, "target_exclusions": exclusions,
            "linux_desktop_qualified": False if desktop else None}


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", default="cargo-audit")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/rust-audit")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    version = run([args.audit, "--version"])
    if version.returncode or version.stdout.strip() != "cargo-audit 0.22.2":
        raise RuntimeError("expected cargo-audit 0.22.2")
    database = run(["git", "-C", str(args.database.resolve()), "rev-parse", "HEAD"])
    if database.returncode:
        raise RuntimeError("advisory database commit unavailable")
    summary = {"tool": version.stdout.strip(), "database_commit": database.stdout.strip(),
               "supported_desktop_targets": list(TARGETS), "locks": {}}
    for name, manifest in (("core", "crates/Cargo.toml"),
                           ("desktop", "apps/desktop/src-tauri/Cargo.toml")):
        lock = str(Path(manifest).with_name("Cargo.lock"))
        audit = run([args.audit, "audit", "--db", str(args.database.resolve()),
                     "--no-fetch", "--file", lock, "--deny", "warnings", "--json"])
        (args.output / f"{name}-audit.json").write_text(audit.stdout, encoding="utf-8")
        (args.output / f"{name}-audit.stderr").write_text(audit.stderr, encoding="utf-8")
        # An audit/tool error must fail rather than masquerade as an empty result.
        if audit.returncode not in (0, 1):
            raise RuntimeError(f"{name} audit failed: {audit.returncode}")
        report = json.loads(audit.stdout)
        trees = {}
        if name == "desktop":
            for target in TARGETS:
                tree = run([args.cargo, "tree", "--locked", "--manifest-path", manifest,
                            "--target", target, "--edges", "normal,build,dev",
                            "--prefix", "none", "--format", "{p}", "--no-dedupe"])
                (args.output / f"desktop-{target}.txt").write_text(
                    tree.stdout, encoding="utf-8")
                if tree.returncode:
                    raise RuntimeError(f"target graph failed: {target}: {tree.stderr}")
                # Cargo adds (*) only with deduplication, disabled above.
                trees[target] = tree.stdout
        result = qualify(report, trees, desktop=name == "desktop")
        result["strict_audit_exit_code"] = audit.returncode
        result["lock_sha256"] = hashlib.sha256((ROOT / lock).read_bytes()).hexdigest()
        summary["locks"][name] = result
    (args.output / "qualification.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all(item["passed"] for item in summary["locks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
