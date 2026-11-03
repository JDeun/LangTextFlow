"""Local CPU translation cache readiness; no implicit Hugging Face download."""
from __future__ import annotations

import importlib.util

from .config import Settings
from .m2m100_cache import m2m100_cache_paths
from .preflight import CheckStatus, PreflightCheck
from .translation.m2m100_ct2 import missing_m2m100_cache_files


def local_m2m100_check(settings: Settings) -> PreflightCheck:
    try:
        missing = missing_m2m100_cache_files(*m2m100_cache_paths(settings))
    except (OSError, ValueError):
        missing = ["managed M2M100 cache requires repair"]
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
            "Use runtime maintenance to explicitly prepare/verify/repair the pinned CPU cache; "
            "custom paths require manual provisioning. The local-mt extra is required."
            if missing else None
        ),
    )
