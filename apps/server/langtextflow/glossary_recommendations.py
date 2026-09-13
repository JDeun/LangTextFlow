from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from .glossary_repository import GlossaryRepository
from .session_repository import SessionRepository

_TOKEN_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9._+\-/]{2,}|[가-힣]{2,}|[\u3040-\u30ff\u4e00-\u9fff]{2,8}",
    re.UNICODE,
)
_STOPWORDS = {
    "this", "that", "with", "from", "have", "will", "your", "about", "there",
    "그리고", "그러나", "입니다", "합니다", "있는", "없는", "우리", "오늘",
}


class GlossaryRecommendation(BaseModel):
    term: str = Field(min_length=2, max_length=160)
    occurrences: int = Field(ge=2)
    sessions: int = Field(ge=1)
    score: float = Field(ge=0)


def recommend_glossary_terms(
    history: SessionRepository,
    glossary: GlossaryRepository,
    *,
    session_limit: int = 20,
    limit: int = 30,
    min_occurrences: int = 2,
) -> list[GlossaryRecommendation]:
    session_limit = max(1, min(session_limit, 100))
    limit = max(1, min(limit, 100))
    min_occurrences = max(2, min(min_occurrences, 50))

    existing = {
        term.casefold()
        for record in glossary.list()
        for term in [record.term, *record.aliases]
        if term.strip()
    }
    counts: Counter[str] = Counter()
    session_counts: Counter[str] = Counter()
    display: dict[str, str] = {}

    for session in history.list_sessions(session_limit):
        seen: set[str] = set()
        for event in history.segments(session.session_id):
            if not event.text:
                continue
            for raw in _TOKEN_RE.findall(event.text):
                token = raw.strip(".,:;!?()[]{}\"'")
                key = token.casefold()
                if len(token) < 2 or len(token) > 80 or key in existing or key in _STOPWORDS:
                    continue
                counts[key] += 1
                seen.add(key)
                display.setdefault(key, token)
        session_counts.update(seen)

    candidates: list[GlossaryRecommendation] = []
    for key, occurrences in counts.items():
        if occurrences < min_occurrences:
            continue
        sessions = session_counts[key]
        score = float(occurrences) + min(sessions, 10) * 1.5
        candidates.append(
            GlossaryRecommendation(
                term=display[key],
                occurrences=occurrences,
                sessions=sessions,
                score=score,
            )
        )
    candidates.sort(key=lambda item: (-item.score, -item.sessions, item.term.casefold()))
    return candidates[:limit]
