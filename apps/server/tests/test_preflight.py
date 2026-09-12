from __future__ import annotations

from langtextflow.preflight import (
    CheckStatus,
    PreflightCheck,
    _blocking_checks,
    _recommended_configuration,
)


def check(check_id: str, status: CheckStatus) -> PreflightCheck:
    return PreflightCheck(
        id=check_id,
        label=check_id,
        status=status,
        summary=check_id,
    )


def base_checks(
    *,
    vibevoice: CheckStatus = CheckStatus.MISSING,
    faster_whisper: CheckStatus = CheckStatus.MISSING,
    whisper_model: CheckStatus = CheckStatus.MISSING,
    ollama: CheckStatus = CheckStatus.MISSING,
    model: CheckStatus = CheckStatus.MISSING,
    openai_compatible: CheckStatus = CheckStatus.MISSING,
    openai_model: CheckStatus = CheckStatus.MISSING,
) -> list[PreflightCheck]:
    return [
        check("vibevoice", vibevoice),
        check("faster-whisper", faster_whisper),
        check("faster-whisper-model", whisper_model),
        check("ollama", ollama),
        check("translation-model", model),
        check("openai-compatible", openai_compatible),
        check("openai-compatible-model", openai_model),
    ]


def test_auto_asr_requires_only_one_ready_provider() -> None:
    checks = base_checks(
        faster_whisper=CheckStatus.READY,
        whisper_model=CheckStatus.READY,
        ollama=CheckStatus.READY,
        model=CheckStatus.READY,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="ollama",
    )

    assert blocking == []


def test_auto_asr_blocks_when_both_providers_are_missing() -> None:
    checks = base_checks(
        ollama=CheckStatus.READY,
        model=CheckStatus.READY,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="ollama",
    )

    assert blocking == ["vibevoice", "faster-whisper"]


def test_explicit_whisper_requires_cached_model() -> None:
    checks = base_checks(
        faster_whisper=CheckStatus.READY,
        whisper_model=CheckStatus.MISSING,
    )

    blocking = _blocking_checks(
        checks,
        engine="faster-whisper",
        translation_provider="none",
    )

    assert blocking == ["faster-whisper-model"]


def test_auto_can_start_on_vibevoice_without_whisper_cache() -> None:
    checks = base_checks(
        vibevoice=CheckStatus.READY,
        faster_whisper=CheckStatus.READY,
        whisper_model=CheckStatus.MISSING,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="none",
    )

    assert blocking == []


def test_explicit_vibevoice_does_not_accept_whisper_as_substitute() -> None:
    checks = base_checks(
        faster_whisper=CheckStatus.READY,
        whisper_model=CheckStatus.READY,
        ollama=CheckStatus.READY,
        model=CheckStatus.READY,
    )

    blocking = _blocking_checks(
        checks,
        engine="vibevoice",
        translation_provider="ollama",
    )

    assert blocking == ["vibevoice"]


def test_translation_none_does_not_require_ollama() -> None:
    checks = base_checks(vibevoice=CheckStatus.READY)

    blocking = _blocking_checks(
        checks,
        engine="vibevoice",
        translation_provider="none",
    )

    assert blocking == []


def test_ollama_translation_requires_service_and_model() -> None:
    checks = base_checks(
        vibevoice=CheckStatus.READY,
        ollama=CheckStatus.READY,
        model=CheckStatus.MISSING,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="ollama",
    )

    assert blocking == ["translation-model"]


def test_openai_compatible_translation_requires_service_and_model() -> None:
    checks = base_checks(
        vibevoice=CheckStatus.READY,
        openai_compatible=CheckStatus.READY,
        openai_model=CheckStatus.MISSING,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="openai-compatible",
    )

    assert blocking == ["openai-compatible-model"]


def test_openai_compatible_translation_is_ready_when_both_checks_pass() -> None:
    checks = base_checks(
        vibevoice=CheckStatus.READY,
        openai_compatible=CheckStatus.READY,
        openai_model=CheckStatus.READY,
    )

    blocking = _blocking_checks(
        checks,
        engine="auto",
        translation_provider="openai-compatible",
    )

    assert blocking == []


def test_recommendation_prefers_auto_when_both_asr_providers_are_ready() -> None:
    recommendation = _recommended_configuration(
        base_checks(
            vibevoice=CheckStatus.READY,
            faster_whisper=CheckStatus.READY,
            whisper_model=CheckStatus.READY,
            ollama=CheckStatus.READY,
            model=CheckStatus.READY,
        ),
        model="translategemma:4b",
    )

    assert recommendation.engine == "auto"
    assert recommendation.translation_provider == "ollama"
    assert recommendation.translation_model == "translategemma:4b"


def test_recommendation_uses_whisper_without_vibevoice() -> None:
    recommendation = _recommended_configuration(
        base_checks(
            faster_whisper=CheckStatus.READY,
            whisper_model=CheckStatus.READY,
        ),
        model="translategemma:4b",
    )

    assert recommendation.engine == "faster-whisper"
    assert recommendation.translation_provider == "none"
    assert recommendation.translation_model is None


def test_recommendation_uses_vibevoice_if_whisper_cache_is_missing() -> None:
    recommendation = _recommended_configuration(
        base_checks(
            vibevoice=CheckStatus.READY,
            faster_whisper=CheckStatus.READY,
            whisper_model=CheckStatus.MISSING,
        ),
        model="translategemma:4b",
    )

    assert recommendation.engine == "vibevoice"


def test_recommendation_does_not_suggest_demo_when_no_real_asr_exists() -> None:
    recommendation = _recommended_configuration(
        base_checks(ollama=CheckStatus.READY, model=CheckStatus.READY),
        model="translategemma:4b",
    )

    assert recommendation.engine is None
    assert recommendation.translation_provider == "ollama"


def test_recommendation_uses_openai_compatible_when_ollama_is_unavailable() -> None:
    recommendation = _recommended_configuration(
        base_checks(
            vibevoice=CheckStatus.READY,
            openai_compatible=CheckStatus.READY,
            openai_model=CheckStatus.READY,
        ),
        model="translategemma:4b",
        openai_model="local-translator",
    )

    assert recommendation.translation_provider == "openai-compatible"
    assert recommendation.translation_model == "local-translator"
