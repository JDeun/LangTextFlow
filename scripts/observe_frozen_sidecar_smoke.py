"""Observe the unchanged full smoke on the plain binary; final cleanup only."""
from __future__ import annotations

import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from scripts.diagnose_frozen_prepare_exit import ROOT, observe, reap_owned, survivors

OUTPUT = ROOT / "artifacts/frozen-full-smoke"


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("Windows diagnostic only")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    binary = ROOT / "apps/desktop/src-tauri/binaries/langtextflow-server-x86_64-pc-windows-msvc.exe"
    original = ROOT / "build/frozen-diagnostic-plain.exe"
    probe = json.loads((ROOT / "artifacts/frozen-prepare-exit/report.json").read_text())
    if not all(item["owned_tree_reaped"] for item in probe["sequence_cleanup"].values()):
        raise RuntimeError("previous diagnostic workers are not confirmed reaped")
    shutil.copy2(original, binary)
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != probe["binary_sha256"]["plain"]:
        raise RuntimeError("plain binary identity changed")
    report = {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                   cwd=ROOT, text=True).strip(),
              "binary_sha256": digest, "binary": "plain original build, no runtime hook",
              "original_smoke": "scripts/smoke_desktop_sidecar.py unchanged",
              "model_downloads": False, "processes": []}
    roots = []
    owned = {}
    stop = threading.Event()
    lock = threading.Lock()
    original_popen = subprocess.Popen

    def observed_popen(command, *args, **kwargs):
        if (isinstance(command, (list, tuple)) and Path(str(command[0])).name == "taskkill"):
            report.setdefault("before_original_taskkill", []).append(survivors(owned))
        process = original_popen(command, *args, **kwargs)
        if isinstance(command, (list, tuple)) and str(command[0]) == str(binary):
            record = {"pid": process.pid, "start_monotonic": time.monotonic(),
                      "flags": [str(arg) for arg in command[1:] if str(arg).startswith("-")],
                      "latest_tree": []}
            with lock:
                roots.append((process, record))
                report["processes"].append(record)
        return process

    def watch() -> None:
        while not stop.is_set():
            with lock:
                current_roots = roots.copy()
            for process, record in current_roots:
                current = observe(process.pid, owned) if process.poll() is None else []
                if current:
                    record["latest_tree"] = current
                    record.setdefault("first_tree", current)
                    if len(current) > len(record.get("largest_tree", [])):
                        record["largest_tree"] = current
                record["returncode"] = process.poll()
            stop.wait(.1)

    observer = threading.Thread(target=watch, daemon=True)
    observer.start()
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        with patch.object(subprocess, "Popen", observed_popen):
            runpy.run_path(str(ROOT / "scripts/smoke_desktop_sidecar.py"), run_name="__main__")
        report["smoke_completed"] = True
    except Exception as exc:
        report["smoke_completed"] = False
        report["error_type"] = type(exc).__name__  # No response/exception body or environment.
    finally:
        stop.set()
        observer.join(timeout=2)
        report["observer_finished"] = not observer.is_alive()
        report["survivors_before_final_cleanup"] = survivors(owned)
        report["final_cleanup"] = reap_owned(owned)
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if (report.get("smoke_completed") and report["observer_finished"]
                 and report["final_cleanup"]["owned_tree_reaped"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
