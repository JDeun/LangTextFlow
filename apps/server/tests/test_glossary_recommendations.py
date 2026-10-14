from types import SimpleNamespace

from langtextflow.glossary_recommendations import recommend_glossary_terms


class FakeHistory:
    def list_sessions(self, limit: int):
        assert limit == 100
        return [
            SimpleNamespace(session_id="a", source_language="ko"),
            SimpleNamespace(session_id="b", source_language="ko"),
            SimpleNamespace(session_id="c", source_language="en"),
        ]

    def segments(self, session_id: str):
        if session_id == "a":
            return [SimpleNamespace(text="LangTextFlow Moses Moses translation")]
        if session_id == "c":
            return [SimpleNamespace(text="Grace Grace Grace")]
        return [SimpleNamespace(text="Moses LangTextFlow captions")]


class FakeGlossary:
    def list(self):
        return [SimpleNamespace(term="LangTextFlow", aliases=[], source_language="ko")]


def test_recommendations_rank_repeated_unregistered_terms() -> None:
    recommendations = recommend_glossary_terms(FakeHistory(), FakeGlossary())  # type: ignore[arg-type]
    assert recommendations
    assert recommendations[0].term == "Moses"
    assert recommendations[0].occurrences == 3
    assert recommendations[0].sessions == 2
    assert all(item.term != "LangTextFlow" for item in recommendations)


def test_recommendations_exclude_unselected_spoken_language() -> None:
    recommendations = recommend_glossary_terms(
        FakeHistory(), FakeGlossary(), source_language="en",  # type: ignore[arg-type]
    )
    assert [item.term for item in recommendations] == ["Grace"]
    korean = recommend_glossary_terms(
        FakeHistory(), FakeGlossary(), source_language="ko",  # type: ignore[arg-type]
    )
    assert all(item.term != "Grace" for item in korean)
