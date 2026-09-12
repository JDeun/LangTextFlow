import re
import unicodedata

from .models import ProductPreset, SessionContext

_CHURCH_ALIASES = {
    "요한 보금": "요한복음",
    "요한 복음": "요한복음",
    "로마 써": "로마서",
    "성녕": "성령",
    "성영": "성령",
    "칭이": "칭의",
    "칭위": "칭의",
    "속재": "속죄",
    "속제": "속죄",
    "십자 가": "십자가",
}


class DeterministicCorrector:
    """Meaning-preserving normalization using only explicit deterministic rules."""

    def correct(self, text: str, context: SessionContext) -> str:
        value = unicodedata.normalize("NFC", text)
        value = re.sub(r"[ \t]+", " ", value).strip()

        aliases: dict[str, str] = {}
        if context.preset is ProductPreset.CHURCH:
            aliases.update(_CHURCH_ALIASES)
        for entry in context.glossary:
            if not entry.enabled:
                continue
            for alias in entry.aliases:
                aliases[alias] = entry.term

        for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
            value = value.replace(alias, canonical)
        return value
