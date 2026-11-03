from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.diagnose_frozen_prepare_exit import ROOT, sanitize


def test_diagnostic_stack_publishes_only_module_basenames() -> None:
    stack = 'File "C:\\owned\\private\\module.py", line 3\nFile "/owned/private/main.py", line 7'
    assert sanitize(stack) == 'File "module.py", line 3\nFile "main.py", line 7'


def test_test_hook_records_source_invalid_exit_without_stdout(tmp_path: Path) -> None:
    log = tmp_path / "trace.log"
    hook = ROOT / "scripts/frozen_prepare_diagnostic_hook.py"
    command = ("import runpy; runpy.run_path(" + repr(str(hook)) + "); "
               "runpy.run_module('langtextflow.m2m100_prepare_worker', run_name='__main__')")
    child = subprocess.run([sys.executable, "-c", command],
                           env={**os.environ, "PYTHONPATH": str(ROOT / "apps/server"),
                                "LTF_TEST_FROZEN_DIAGNOSTIC_LOG": str(log)},
                           capture_output=True, timeout=10)
    assert child.returncode == 2 and not child.stdout and not child.stderr
    markers = {json.loads(line)["marker"] for line in log.read_text().splitlines()}
    assert {"runtime_hook_entered", "m2m100_prepare_worker.py:<module>:entered",
            "m2m100_prepare_worker.py:main:entered", "invalid_args_check_before",
            "m2m100_prepare_worker.py:SystemExit:2", "python_atexit"} <= markers
