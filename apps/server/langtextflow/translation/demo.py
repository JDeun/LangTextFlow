from ..models import SessionContext
from .base import Translator

_ENGLISH_DEMO = {
    "오늘 우리가 볼 말씀은 요한복음 삼장입니다": "Today we will look at John chapter 3.",
    "하나님이 세상을 이처럼 사랑하사 독생자를 주셨으니": (
        "For God so loved the world that He gave His one and only Son."
    ),
    "오늘 말씀을 통해 복음의 의미를 함께 살펴보겠습니다": (
        "Today, we will consider the meaning of the gospel together."
    ),
}


class DemoTranslator(Translator):
    provider = "demo"
    model = "deterministic-demo"

    async def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        context: SessionContext,
    ) -> str:
        del source_language, context
        if target_language == "en" and text in _ENGLISH_DEMO:
            return _ENGLISH_DEMO[text]
        return f"[{target_language.upper()} demo] {text}"
