"""Local CPU translation cache readiness; no implicit Hugging Face download."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .config import Settings
from .preflight import CheckStatus, PreflightCheck


def local_m2m100_check(settings: Settings) -> PreflightCheck:
    model = Path(settings.local_m2m100_ct2_dir)
    tokenizer = Path(settings.local_m2m100_tokenizer_dir)
    missing: list[str] = []
    if not model.is_dir() or not (model / "model.bin").is_file():
        missing.append("CTranslate2 converted model.bin")
    if not tokenizer.is_dir() or not (
        (tokenizer / "tokenizer_config.json").is_file()
    ):
        missing.append("M2M100 tokenizer files")
    if importlib.util.find_spec("ctranslate2") is None:
        missing.append("ctranslate2 optional Python dependency")
    if importlib.util.find_spec("transformers") is None:
        missing.append("transformers optional Python dependency")
    if importlib.util.find_spec("sentencepiece") is None:
        missing.append("sentencepiece optional Python dependency")
    return PreflightCheck(
        id="m2m100-local",
        label="로컬 M2M100 CPU 모델 (실험적)",
        status=CheckStatus.MISSING if missing else CheckStatus.READY,
        summary=("Local M2M100 CT2 model and tokenizer detected"
                 if not missing else "Missing: " + ", ".join(missing)),
        recommendation=(
            "Provision the pinned M2M100 CT2 CPU INT8 model/tokenizer and "
            "install optional dependencies locally. No network fallback."
            if missing else None
        ),
    )
