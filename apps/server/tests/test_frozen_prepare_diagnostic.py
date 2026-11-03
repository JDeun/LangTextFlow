from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import diagnose_frozen_prepare_exit as diagnostic
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture; Windows real binary CI")
def test_successful_case_defers_descendant_cleanup(tmp_path, monkeypatch) -> None:
    fixture = tmp_path / "fixture"
    fixture.write_text(
        f"#!{sys.executable}\n"
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "time.sleep(.5)\nraise SystemExit(2)\n"
    )
    fixture.chmod(0o700)
    output = tmp_path / "out"
    output.mkdir()
    monkeypatch.setattr(diagnostic, "OUTPUT", output)
    owned = {}
    try:
        result = diagnostic.case(fixture, tmp_path / "app", 0, False, False, True,
                                 arguments=[], expected_code=2, managed_pipe=True,
                                 sequence_owned=owned)
        assert result["returncode"] == 2 and result["cleanup_deferred_to_sequence_end"]
        assert result["survivors_after_case"], "success must not erase original descendant state"
        assert "failure_cleanup" not in result
        cleanup = diagnostic.reap_owned(owned)
        assert cleanup["forced_kills"] and cleanup["owned_tree_reaped"]
    finally:
        diagnostic.reap_owned(owned)
