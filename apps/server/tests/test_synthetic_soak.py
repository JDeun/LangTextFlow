import asyncio

import pytest

from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_repeated_mock_sessions_do_not_leak_named_runtime_tasks(tmp_path) -> None:
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "soak.db")))
    await runtime.initialize()
    request = StartSessionRequest(
        engine="mock",
        source_language="ko",
        target_languages=["en"],
        translation_provider="none",
    )

    for _ in range(50):
        await runtime.start(request)
        await asyncio.sleep(0)
        await runtime.stop()
        assert runtime._persistence_task is None
        assert runtime.metrics.persistence_queue_depth == 0

    await runtime.shutdown()
    await asyncio.sleep(0)

    leaked = [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and not task.done()
        and (
            task.get_name().startswith("langtextflow")
            or task.get_name().startswith("caption-")
            or task.get_name().startswith("mock-")
            or task.get_name().startswith("persistence-")
        )
    ]
    assert leaked == []
