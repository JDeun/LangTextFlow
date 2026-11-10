"""Provider construction shared by the caption pipeline and immutable leaves."""
from __future__ import annotations

from ..config import Settings
from ..models import StartSessionRequest
from . import DemoTranslator, M2M100CT2Translator, OllamaTranslator, OpenAICompatibleTranslator
from .base import Translator


def validate_local_pairs(request: StartSessionRequest) -> None:
    if request.translation_provider == "m2m100-ct2" and (
        request.source_language not in {"ko", "en", "ja"}
        or any(target not in {"ko", "en", "ja"} or target == request.source_language
               for target in request.target_languages)
    ):
        raise ValueError("Local M2M100 supports only the six directed ko/en/ja pairs")


def provider_options(settings: Settings, request: StartSessionRequest) -> dict:
    provider = request.translation_provider
    if provider == "m2m100-ct2":
        validate_local_pairs(request)
        from ..m2m100_cache import m2m100_cache_paths

        model_dir, tokenizer_dir = m2m100_cache_paths(settings)
        return {"provider": provider, "model_dir": str(model_dir),
                "tokenizer_dir": str(tokenizer_dir), "cpu_threads": (
                    settings.low_resource_cpu_threads
                    if request.resource_profile == "low" or settings.resource_profile == "low"
                    else settings.local_m2m100_cpu_threads)}
    if provider in {"none", "demo"}:
        return {"provider": provider}
    if provider == "ollama":
        return {"provider": provider, "url": settings.ollama_url,
                "model": request.translation_model or settings.ollama_translation_model,
                "api_key": "", "timeout_seconds": settings.ollama_translation_timeout_seconds}
    if provider == "openai-compatible":
        return {"provider": provider, "url": settings.openai_compatible_url,
                "model": request.translation_model or settings.openai_compatible_translation_model,
                "api_key": settings.openai_compatible_api_key,
                "timeout_seconds": max(settings.openai_compatible_timeout_seconds, .1)}
    raise ValueError(f"unsupported translation provider: {provider}")


def create_translator(options: dict) -> Translator | None:
    provider = options["provider"]
    if provider == "none":
        return None
    if provider == "demo":
        return DemoTranslator()
    if provider == "m2m100-ct2":
        return M2M100CT2Translator(**{key: options[key] for key in (
            "model_dir", "tokenizer_dir", "cpu_threads",
        )})
    common = {"base_url": options["url"], "model": options["model"],
              "request_timeout_seconds": options["timeout_seconds"]}
    if provider == "ollama":
        return OllamaTranslator(**common)
    if provider == "openai-compatible":
        return OpenAICompatibleTranslator(**common, api_key=options["api_key"])
    raise ValueError(f"unsupported translation provider: {provider}")


def build_translator(settings: Settings, request: StartSessionRequest) -> Translator | None:
    return create_translator(provider_options(settings, request))
