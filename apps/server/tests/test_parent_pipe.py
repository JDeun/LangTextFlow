"""Real sole-reader pipe boundaries, with no models or external processes."""
from __future__ import annotations

import concurrent.futures
import os
import time

import pytest

from langtextflow.parent_pipe import read_parent_pipe


def test_parent_pipe_waits_for_data_then_reports_eof():
    reader, writer = os.pipe()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(read_parent_pipe, 2, descriptor=reader)
            time.sleep(.05)
            assert not pending.done()  # A temporarily empty pipe is not EOF.
            os.write(writer, b"abc")
            assert pending.result(timeout=2) == b"ab"
            assert read_parent_pipe(16, descriptor=reader) == b"c"
            os.close(writer)
            writer = None
            assert read_parent_pipe(16, descriptor=reader) == b""
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle-query failure boundary")
def test_parent_pipe_invalid_windows_handle_is_terminal():
    assert read_parent_pipe(1, descriptor=-1) == b""


def test_parent_pipe_rejects_empty_reads():
    with pytest.raises(ValueError):
        read_parent_pipe(0)
