from pathlib import Path

import pytest

from langtextflow.config import Settings
from langtextflow.model_cache import ModelCacheManager


@pytest.mark.asyncio
async def test_inventory_and_clear_are_scoped_to_configured_cache(tmp_path: Path) -> None:
    settings = Settings(model_cache_dir=str(tmp_path / "models"))
    manager = ModelCacheManager(settings)
    file_path = tmp_path / "models" / "huggingface" / "blob.bin"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"12345")

    inventory = await manager.inventory()
    assert inventory.total_bytes == 5
    assert next(item for item in inventory.entries if item.area == "huggingface").files == 1

    cleared = await manager.clear("huggingface")
    assert cleared.total_bytes == 0
    assert (tmp_path / "models" / "huggingface").is_dir()


@pytest.mark.asyncio
async def test_clear_rejects_unknown_cache_area(tmp_path: Path) -> None:
    manager = ModelCacheManager(Settings(model_cache_dir=str(tmp_path / "models")))
    with pytest.raises(ValueError, match="unsupported cache area"):
        await manager.clear("../outside")
