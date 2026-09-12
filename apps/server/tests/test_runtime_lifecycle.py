import pytest

from langtextflow.config import Settings
from langtextflow.models import StartSessionRequest
from langtextflow.runtime import CaptionRuntime


@pytest.mark.asyncio
async def test_persistence_worker_exists_only_for_active_session(tmp_path) -> None:
    runtime = CaptionRuntime(
        Settings(database_path=str(tmp_path / "runtime.db"))
    )

    await runtime.initialize()
    assert runtime._persistence_task is None

    await runtime.start(
        StartSessionRequest(
            engine="mock",
            source_language="ko",
            target_languages=["en"],
            translation_provider="none",
        )
    )
    assert runtime._persistence_task is not None

    await runtime.stop()
    assert runtime._persistence_task is None
    assert runtime.metrics.persistence_queue_depth == 0

    await runtime.shutdown()
    assert runtime._persistence_task is None
