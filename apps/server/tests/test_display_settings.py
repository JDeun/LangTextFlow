from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from langtextflow.config import Settings
from langtextflow.models import (
    CaptionDisplaySettings,
    CaptionFontFamily,
    CaptionTextAlign,
    ProductPreset,
    SessionContext,
    SessionState,
)
from langtextflow.runtime import CaptionRuntime


def test_display_settings_defaults_are_safe_for_live_captions() -> None:
    settings = CaptionDisplaySettings()

    assert settings.font_family is CaptionFontFamily.SYSTEM
    assert settings.font_scale_percent == 100
    assert settings.max_lines == 2
    assert settings.hold_seconds == 8.0
    assert settings.show_source_when_translated is True
    assert settings.text_align is CaptionTextAlign.CENTER


def test_display_settings_enforce_product_bounds() -> None:
    with pytest.raises(ValidationError):
        CaptionDisplaySettings(font_scale_percent=181)
    with pytest.raises(ValidationError):
        CaptionDisplaySettings(max_lines=5)
    with pytest.raises(ValidationError):
        CaptionDisplaySettings(hold_seconds=31)


def test_audience_view_receives_session_display_profile(tmp_path) -> None:
    profile = CaptionDisplaySettings(
        font_family=CaptionFontFamily.SERIF,
        font_scale_percent=130,
        max_lines=3,
        hold_seconds=12,
        show_source_when_translated=False,
        text_align=CaptionTextAlign.LEFT,
    )
    context = SessionContext(
        title="Mission Conference",
        preset=ProductPreset.CHURCH,
        display_settings=profile,
    )
    runtime = CaptionRuntime(Settings(database_path=str(tmp_path / "display.db")))
    runtime.state = SessionState(
        session_id="session-1",
        join_code="ABC123",
        running=True,
        source_language="en",
        target_languages=["ko"],
        context=context,
        started_at=datetime.now(UTC),
    )

    view = runtime.audience_view("abc123")

    assert view.display_settings == profile
    assert view.source_language == "en"
    assert view.target_languages == ["ko"]
