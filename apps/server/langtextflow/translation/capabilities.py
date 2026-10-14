"""Provider capability contract for the operator UI and local CPU model gating.

Capability metadata is a DECLARATION, not a model quality report.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ..config import Settings
from ..local_mt_preflight import local_m2m100_check

_LANGUAGES = ("ko", "en", "ja")


class TranslationCapability(BaseModel):
    provider: str
    label: str
    local_only: bool
    requires_model: bool
    # None means the provider did not declare an exhaustive pair set.
    supported_pairs: list[tuple[str, str]] | None = None
    configured: bool | None = None
    field_qualified: bool = False
    glossary_mode: str = "unsupported"
    model_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _loopback_endpoint(url: str) -> bool:
    # An Ollama/OpenAI-compatible proxy can be configured remotely.
    # Never label such an endpoint offline or local-only by provider name.
    parsed = urlsplit(url)
    return (parsed.scheme in {"http", "https"}
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"})


def translation_capabilities(settings: Settings) -> list[TranslationCapability]:
    model = local_m2m100_check(settings)
    pairs = [
        (source, target) for source in _LANGUAGES
        for target in _LANGUAGES if source != target
    ]
    return [
        TranslationCapability(
            provider="m2m100-ct2",
            label="M2M100 CT2 CPU (experimental local model)",
            local_only=True,
            requires_model=True,
            supported_pairs=pairs,
            configured=model.status == "ready",
            field_qualified=False,
            glossary_mode="unsupported",
            model_id="facebook/m2m100_418M",
            warnings=[
                "Experimental routing only; not qualified for sermon interpretation.",
                "No automatic download; native CPU inference is non-preemptible.",
                "Process-wide execution budget: one CPU worker, at most four admitted jobs.",
            ],
        ),
        TranslationCapability(
            provider="ollama",
            label="Ollama (operator-configured endpoint)",
            local_only=_loopback_endpoint(settings.ollama_url),
            requires_model=True,
            configured=None,
            glossary_mode="prompt-hints-not-guaranteed",
            warnings=["Supported language pairs require independent model-level verification."],
        ),
        TranslationCapability(
            provider="openai-compatible",
            label="OpenAI-compatible endpoint (configured by operator)",
            local_only=_loopback_endpoint(settings.openai_compatible_url),
            requires_model=True,
            configured=None,
            glossary_mode="prompt-hints-not-guaranteed",
            warnings=[
                "An operator-configured endpoint may be remote. "
                "Do not assume privacy, offline operation or language support."
            ],
        ),
        TranslationCapability(
            provider="demo",
            label="Demo translator (not an actual language model)",
            local_only=True,
            requires_model=False,
            configured=True,
            glossary_mode="unsupported",
            warnings=["Demo output is not a real translation."],
        ),
        TranslationCapability(
            provider="none",
            label="No translation",
            local_only=True,
            requires_model=False,
            supported_pairs=[],
            configured=True,
            warnings=["Only source-language captions are available."],
        ),
    ]
