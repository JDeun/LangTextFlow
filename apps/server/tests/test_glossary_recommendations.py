from types import SimpleNamespace

from langtextflow.glossary_recommendations import recommend_glossary_terms


class FakeHistory:
    def list_sessions(self, limit: int):
        assert limit == 20
        return [SimpleNamespace(session_id="a"), SimpleNamespace(session_id="b")]

    def segments(self, session_id: str):
        if session_id == "a":
            return [SimpleNamespace(text="LangTextFlow Moses Moses translation")]
        return [SimpleNamespace(text="Moses LangTextFlow captions")]


class FakeGlossary:
    def list(self):
        return [SimpleNamespace(term="LangTextFlow", aliases=[])]


def test_recommendations_rank_repeated_unregistered_terms() -> None:
    recommendations = recommend_glossary_terms(FakeHistory(), FakeGlossary())  # type: ignore[arg-type]
    assert recommendations
    assert recommendations[0].term == "Moses"
    assert recommendations[0].occurrences == 3
    assert recommendations[0].sessions == 2
    assert all(item.term != "LangTextFlow" for item in recommendations)
