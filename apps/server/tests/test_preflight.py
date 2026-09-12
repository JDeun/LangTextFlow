from __future__ import annotations

from langtextflow.preflight import CheckStatus, PreflightCheck, _blocking_checks


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
    ollama: CheckStatus = CheckStatus.MISSING,
    model: CheckStatus = CheckStatus.MISSING,
) -> list[PreflightCheck]:
    return [
        check("vibevoice", vibevoice),
        check("faster-whisper", faster_whisper),
        check("ollama", ollama),
        check("translation-model", model),
    ]


def test_auto_asr_requires_only_one_ready_provider() -> None:
    checks = base_checks(
        faster_whisper=CheckStatus.READY,
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


def test_explicit_vibevoice_does_not_accept_whisper_as_substitute() -> None:
    checks = base_checks(
        faster_whisper=CheckStatus.READY,
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
