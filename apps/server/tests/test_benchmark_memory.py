"""Parent RSS keeps its meaning when native work moves into actual child processes."""
from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from langtextflow.benchmark import MemoryTracker


class ProcessError(Exception):
    pass


class FakeProcess:
    def __init__(self, pid, rss_mb=10, children=()):
        self.pid = pid
        self.rss = rss_mb * 1024 * 1024
        self.descendants = list(children)
        self.error = None

    def memory_info(self):
        if self.error == "memory":
            raise ProcessError
        return SimpleNamespace(rss=self.rss)

    def children(self, *, recursive):
        assert recursive is False
        if self.error == "discovery":
            raise ProcessError
        return self.descendants

    def is_running(self):
        return self.error != "exited"


def fake_psutil(monkeypatch, root):
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        Process=lambda: root, Error=ProcessError, NoSuchProcess=ProcessError,
    ))


def test_memory_reports_parent_and_recursive_children_separately(monkeypatch):
    root = FakeProcess(1, 10, [FakeProcess(2, 20, [FakeProcess(3, 30)])])
    fake_psutil(monkeypatch, root)
    memory = MemoryTracker()
    memory.start()
    report = memory.report()
    assert report["rss_peak_mb"] == 10 and report["samples"][0]["rss_mb"] == 10
    assert report["scope"] == "parent_process"
    tree = report["process_tree"]
    assert tree["rss_peak_mb"] == 60 and tree["complete"]
    assert tree["samples"][0]["process_count"] == 3
    assert tree["method"] == "psutil_sum_sampled_rss_not_pss"
    assert tree["shared_pages_counted_per_process"] and not tree["external_services_included"]


@pytest.mark.parametrize("failure", ["memory", "exited", "discovery"])
def test_disappeared_or_inaccessible_children_are_partial_not_silent_zero(monkeypatch, failure):
    child = FakeProcess(2, 30)
    child.error = failure
    fake_psutil(monkeypatch, FakeProcess(1, 10, [child]))
    memory = MemoryTracker()
    memory.start()
    report = memory.report()
    assert report["rss_peak_mb"] == 10 and report["errors"]
    tree = report["process_tree"]
    assert not tree["complete"] and tree["rss_peak_mb"] is None
    assert tree["observed_rss_peak_mb"] in (10, 40) and tree["partial_sample_count"] == 1


def test_parent_query_failure_and_missing_dependency_do_not_break_benchmark(monkeypatch):
    root = FakeProcess(1)
    root.error = "memory"
    fake_psutil(monkeypatch, root)
    memory = MemoryTracker()
    memory.start()
    assert memory.report()["rss_peak_mb"] is None
    assert memory.report()["errors"] == {"parent_unavailable": 1}
    monkeypatch.setitem(sys.modules, "psutil", None)
    absent = MemoryTracker()
    absent.start()
    report = absent.report()
    assert not report["available"] and report["rss_peak_mb"] is None
    assert report["rss_growth_mb"] is None and report["samples"] == []
    assert not report["process_tree"]["available"]


def test_sampling_storage_and_process_visits_are_bounded_without_losing_lifetime_peak(monkeypatch):
    root = FakeProcess(1)
    fake_psutil(monkeypatch, root)
    monkeypatch.setattr(MemoryTracker, "MAX_SAMPLES", 3)
    memory = MemoryTracker()
    memory.start()
    for rss in (1000, 11, 12, 13, 14):
        root.rss = rss * 1024 * 1024
        memory.sample(force=True)
    report = memory.report()
    assert len(report["samples"]) == len(report["process_tree"]["samples"]) == 3
    assert report["sample_count"] == 6 and report["rss_peak_mb"] == 1000
    assert report["rss_growth_mb"] == 4 and report["process_tree"]["rss_peak_mb"] == 1000
    monkeypatch.setattr(MemoryTracker, "MAX_PROCESSES", 2)
    root.descendants = [FakeProcess(pid) for pid in (2, 3, 4)]
    bounded = MemoryTracker()
    bounded.start()
    tree = bounded.report()["process_tree"]
    assert tree["samples"][0]["process_count"] == 2
    assert not tree["complete"] and tree["rss_peak_mb"] is None
    assert bounded.report()["errors"] == {"process_limit": 1}


def test_real_allocating_child_is_visible_in_tree_but_not_parent_rss(tmp_path):
    psutil = pytest.importorskip("psutil")
    ready = tmp_path / "ready"
    process = subprocess.Popen([
        sys.executable, "-c",
        "import pathlib,sys; data=bytearray(32*1024*1024); "
        "data[::4096]=b'x'*len(data[::4096]); "
        "pathlib.Path(sys.argv[1]).write_text('ready'); sys.stdin.buffer.read(1)",
        str(ready),
    ], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "synthetic allocating child did not initialize"
        memory = MemoryTracker(interval_seconds=.1)
        memory.start()
        report = memory.report()
        measured_parent = report["samples"][-1]["rss_mb"]
        actual_parent = psutil.Process().memory_info().rss / (1024 * 1024)
        assert abs(measured_parent - actual_parent) < 4
        tree = report["process_tree"]
        assert tree["complete"] and tree["rss_peak_mb"] - report["rss_peak_mb"] >= 25
        assert tree["samples"][-1]["process_count"] >= 2
        process.stdin.close()
        process.wait(timeout=3)
        memory.sample(force=True)
        after = memory.report()
        assert after["process_tree"]["samples"][-1]["process_count"] == 1
        assert after["process_tree"]["rss_peak_mb"] >= tree["rss_peak_mb"]
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
