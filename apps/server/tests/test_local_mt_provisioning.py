"""Provisioning fails closed before any network/dependency access."""
import importlib.util
from pathlib import Path

import pytest


def _load_provisioner():
    path = Path(__file__).resolve().parents[3] / "scripts" / "provision_m2m100_ct2.py"
    spec = importlib.util.spec_from_file_location("ltf_provision_m2m100", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_must_be_immutable(tmp_path):
    provision = _load_provisioner().provision
    for revision in ("", "main", "latest", "master"):
        with pytest.raises(ValueError, match="immutable"):
            provision(model_dir=tmp_path / "model",
                      tokenizer_dir=tmp_path / "tokenizer", revision=revision)


def test_existing_cache_is_not_overwritten(tmp_path):
    provision = _load_provisioner().provision
    existing = tmp_path / "model"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        provision(model_dir=existing, tokenizer_dir=tmp_path / "tokenizer",
                  revision="0123456789abcdef")
