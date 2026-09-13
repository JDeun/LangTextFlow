from __future__ import annotations

import pytest
from pydantic import ValidationError

from langtextflow.config import Settings


def test_settings_reject_invalid_ports() -> None:
    with pytest.raises(ValidationError):
        Settings(backend_port=0)
    with pytest.raises(ValidationError):
        Settings(frontend_port=65536)


def test_settings_reject_unaligned_audio_frame_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(max_audio_frame_bytes=1025)


def test_settings_accept_small_aligned_audio_frame_limit() -> None:
    settings = Settings(max_audio_frame_bytes=4096)
    assert settings.max_audio_frame_bytes == 4096


def test_settings_reject_zero_asr_replay_window() -> None:
    with pytest.raises(ValidationError):
        Settings(asr_replay_seconds=0)
