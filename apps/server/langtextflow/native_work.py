"""Bound non-preemptible native work across cancelled/restarted sessions."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Any, Literal, TypeVar

_T = TypeVar("_T")
_SLOTS = {provider: BoundedSemaphore(1) for provider in ("whisper", "m2m100-load")}
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="langtextflow-native")


async def run_native_work(
    provider: Literal["whisper", "m2m100-load"], work: Callable[..., _T], *args: Any,
) -> _T:
    slot = _SLOTS[provider]
    if not slot.acquire(blocking=False):
        raise RuntimeError(f"{provider} native work is still busy; retry after it completes")
    try:
        job = _EXECUTOR.submit(work, *args)
    except BaseException:
        slot.release()
        raise
    job.add_done_callback(lambda _: slot.release())
    future = asyncio.wrap_future(job)
    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    # shortcut: native work is not preemptible, use process workers for hard termination.
    return await asyncio.shield(future)
