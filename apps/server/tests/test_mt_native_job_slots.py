"""Native inference capacity accounting survives cancelled queued futures."""
from __future__ import annotations

from concurrent.futures import Future
from threading import BoundedSemaphore
from unittest.mock import patch

from langtextflow.translation import m2m100_ct2


def test_cancelled_native_future_releases_admission_slot() -> None:
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    job: Future[str] = Future()
    assert job.cancel()
    with patch.object(m2m100_ct2, "_NATIVE_JOB_SLOTS", slots):
        m2m100_ct2._release_native_job_slot(job)
    assert slots.acquire(blocking=False)
    slots.release()


def test_failed_native_future_releases_admission_slot() -> None:
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    job: Future[str] = Future()
    job.set_exception(RuntimeError("native inference failed"))
    with patch.object(m2m100_ct2, "_NATIVE_JOB_SLOTS", slots):
        m2m100_ct2._release_native_job_slot(job)
    assert slots.acquire(blocking=False)
    slots.release()
