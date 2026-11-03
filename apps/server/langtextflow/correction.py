import re
import unicodedata

from .correction_safety import preserves_alias_tokens, protected_tokens
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

    def correct(
        self, text: str, context: SessionContext, *,
        source_language: str = "ko",
    ) -> str:
        """Only apply operator alias rewrites for the verified source language.

        The Korean default retains direct legacy API callers; the runtime
        always supplies the actual StartSessionRequest.source_language.
        """
        language = source_language.strip().lower().replace("_", "-")
        value = unicodedata.normalize("NFC", text)
        value = re.sub(r"[ \t]+", " ", value).strip()

        aliases: dict[str, str] = {}
        if context.preset is ProductPreset.CHURCH and language == "ko":
            aliases.update(_CHURCH_ALIASES)
        for entry in context.glossary:
            if (not entry.enabled or entry.source_language != language
                    or not entry.applies_to(context.preset)):
                continue
            for alias in entry.aliases:
                aliases[alias] = entry.term

        ordered_aliases = sorted(
            aliases.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        protected = protected_tokens(value, language)
        for alias, canonical in ordered_aliases:
            if not preserves_alias_tokens(alias, canonical, language):
                continue
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9\s.'-]*", alias):
                # Never rewrite 'cross' inside 'crossing' or 'act' inside 'action'.
                pattern = r"(?<![a-zA-Z0-9])" + re.escape(alias) + r"(?![a-zA-Z0-9])"
                candidate = re.sub(
                    pattern,
                    lambda _, replacement=canonical: replacement,
                    value,
                    flags=re.IGNORECASE,
                )
            else:
                candidate = value.replace(alias, canonical)
            if candidate != value and protected_tokens(candidate, language) == protected:
                value = candidate
        return value
