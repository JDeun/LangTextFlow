"""Audit both complete locks; qualify only evidenced Windows/macOS GTK exclusions."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("x86_64-pc-windows-msvc", "aarch64-apple-darwin", "x86_64-apple-darwin")
# These remain findings in the complete lock. Linux desktop is NOT qualified.
LINUX_WARNINGS = {
    ("RUSTSEC-2024-0429", "glib", "0.18.5", "unsound"),
    ("RUSTSEC-2024-0370", "proc-macro-error", "1.0.4", "unmaintained"),
}
MANIFESTS = {"core": "crates/Cargo.toml", "desktop": "apps/desktop/src-tauri/Cargo.toml"}
AUDIT_CONFIG = "[yanked]\nenabled = true\nupdate_index = true\n[output]\nquiet = false\n"


def contains_package(tree: str, name: str, version: str) -> bool:
    return any(line.split()[:2] == [name, f"v{version}"] for line in tree.splitlines())


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
            advisory = warning.get("advisory") or {}
            key = (advisory.get("id", kind), package["name"], package["version"], kind)
            if not desktop or key not in LINUX_WARNINGS:
                errors.append(f"unqualified_warning:{key[0]}")
                continue
            if set(trees) != set(TARGETS):
                errors.append("missing_supported_target_graph")
                continue
            needle = f"{package['name']} v{package['version']}"
            present = [target for target, tree in trees.items()
                       if contains_package(tree, package["name"], package["version"])]
            if present:
                errors.append(f"supported_target_warning:{key[0]}:{','.join(present)}")
            else:
                exclusions.append({"advisory": key[0], "package": needle,
                                   "reason": "absent_from_all_supported_target_graphs"})
    return {"passed": not errors, "errors": errors, "target_exclusions": exclusions,
            "linux_desktop_qualified": False if desktop else None}


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd or ROOT, capture_output=True, text=True, timeout=300)


def lock_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / Path(path).with_name("Cargo.lock")).read_bytes())
            .hexdigest() for name, path in MANIFESTS.items()}


def checked(command: list[str], hashes: dict[str, str], *,
            cwd: Path | None = None) -> subprocess.CompletedProcess:
    if lock_hashes() != hashes:
        raise RuntimeError("lock_changed_before_subprocess")
    result = run(command, cwd=cwd)
    if lock_hashes() != hashes:
        raise RuntimeError("lock_changed_during_subprocess")
    return result


def audit_locks(args: argparse.Namespace, controlled: Path) -> int:
    hashes = lock_hashes()
    version = checked([args.audit, "--version"], hashes, cwd=controlled)
    if version.returncode or version.stdout.strip() != "cargo-audit 0.22.2":
        raise RuntimeError("expected cargo-audit 0.22.2")
    database = checked(["git", "-C", str(args.database.resolve()), "rev-parse", "HEAD"], hashes)
    if database.returncode:
        raise RuntimeError("advisory database commit unavailable")
    summary = {"tool": version.stdout.strip(), "database_commit": database.stdout.strip(),
               "cross_target_graphs": list(TARGETS), "native_graph_gate": "platform-smoke",
               "controlled_yanked_check": True, "locks": {}}
    for name, manifest in MANIFESTS.items():
        manifest = str(ROOT / manifest)
        lock = str(Path(manifest).with_name("Cargo.lock"))
        # --no-fetch also disables cargo-audit's index update. Populate every locked
        # package first; a partial cache must never look like a completed yank check.
        fetched = checked([args.cargo, "fetch", "--color", "never", "--locked",
                           "--manifest-path", manifest], hashes)
        (args.output / f"{name}-registry-preparation.txt").write_text(
            fetched.stdout + fetched.stderr, encoding="utf-8")
        if fetched.returncode:
            raise RuntimeError(f"{name}_registry_preparation_failed")
        command = [args.audit, "audit", "--color", "never", "--db", str(args.database.resolve()),
                   "--no-fetch", "--file", lock, "--deny", "warnings"]
        audit = checked([*command, "--json"], hashes, cwd=controlled)
        (args.output / f"{name}-audit.json").write_text(audit.stdout, encoding="utf-8")
        (args.output / f"{name}-audit.stderr").write_text(audit.stderr, encoding="utf-8")
        # An audit/tool error must fail rather than masquerade as an empty result.
        if audit.returncode not in (0, 1) or audit.stderr.strip():
            raise RuntimeError(f"{name} audit failed: {audit.returncode}")
        report = json.loads(audit.stdout)
        # JSON mode suppresses index-open warnings. A nonquiet companion exposes
        # them; the controlled config prevents home/project yanked=false bypasses.
        terminal = checked([*command, "--format", "terminal"], hashes, cwd=controlled)
        diagnostic = terminal.stdout + terminal.stderr
        (args.output / f"{name}-audit-terminal.txt").write_text(diagnostic, encoding="utf-8")
        lines = [line.strip() for line in terminal.stderr.splitlines() if line.strip()]
        warning_count = sum(len(items) for items in report["warnings"].values())
        vulnerability_count = report["vulnerabilities"]["count"]
        loaded = r"Loaded \d+ security advisories \(from .+\)"
        scanning = r"Scanning .+ for vulnerabilities \(\d+ crate dependencies\)"
        allowed = [loaded, scanning,
                   rf"error: {warning_count} denied warnings? found!",
                   rf"error: {vulnerability_count} vulnerabilit(?:y|ies) found!"]
        if (terminal.returncode not in (0, 1)
                or not any(re.fullmatch(loaded, line) for line in lines)
                or not any(re.fullmatch(scanning, line) for line in lines)
                or any(not any(re.fullmatch(pattern, line) for pattern in allowed)
                       for line in lines)):
            raise RuntimeError(f"{name}_registry_check_failed")
        trees = {}
        if name == "desktop":
            for target in TARGETS:
                tree = checked([args.cargo, "tree", "--color", "never", "--locked",
                            "--manifest-path", manifest,
                            "--target", target, "--edges", "normal,build,dev",
                            "--prefix", "none", "--format", "{p}", "--no-dedupe"], hashes)
                (args.output / f"desktop-{target}.txt").write_text(
                    tree.stdout, encoding="utf-8")
                if tree.returncode:
                    raise RuntimeError(f"target graph failed: {target}: {tree.stderr}")
                # Cargo adds (*) only with deduplication, disabled above.
                trees[target] = tree.stdout
        result = qualify(report, trees, desktop=name == "desktop")
        result["strict_audit_exit_code"] = audit.returncode
        result["strict_terminal_exit_code"] = terminal.returncode
        result["lock_sha256"] = hashes[name]
        summary["locks"][name] = result
    (args.output / "qualification.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all(item["passed"] for item in summary["locks"].values()) else 1


def native_graph(args: argparse.Namespace) -> int:
    hashes = lock_hashes()
    host = checked([args.rustc, "-vV"], hashes)
    target = next((line.removeprefix("host: ") for line in host.stdout.splitlines()
                   if line.startswith("host: ")), None)
    if host.returncode or target not in TARGETS:
        raise RuntimeError("unsupported_native_desktop_host")
    tree = checked([args.cargo, "tree", "--color", "never", "--locked", "--manifest-path",
                    str(ROOT / MANIFESTS["desktop"]), "--target", target,
                    "--edges", "normal,build,dev", "--prefix", "none", "--format", "{p}",
                    "--no-dedupe"], hashes)
    (args.output / f"native-{target}.txt").write_text(tree.stdout, encoding="utf-8")
    if tree.returncode or any(contains_package(tree.stdout, name, version)
                             for _, name, version, _ in LINUX_WARNINGS):
        raise RuntimeError("native_desktop_warning_graph_failed")
    (args.output / "native-graph.json").write_text(json.dumps({
        "host_target": target, "native_host": True, "lock_sha256": hashes["desktop"],
        "gtk_warning_packages_absent": True,
    }, indent=2) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", default="cargo-audit")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--rustc", default="rustc")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--native-desktop-graph", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/rust-audit")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        if args.native_desktop_graph:
            return native_graph(args)
        if args.database is None:
            parser.error("--database is required for complete lock audits")
        with tempfile.TemporaryDirectory(prefix="ltf-audit-config-") as directory:
            controlled = Path(directory)
            (controlled / ".cargo").mkdir()
            (controlled / ".cargo/audit.toml").write_text(AUDIT_CONFIG, encoding="utf-8")
            return audit_locks(args, controlled)
    except (
        RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
    ) as exc:
        (args.output / "qualification.json").write_text(json.dumps({
            "passed": False, "error": str(exc), "error_type": type(exc).__name__,
        }, indent=2) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
