"""Project terminology overlays: scoping, snapshot precedence, deletion and API security."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from langtextflow import main as main_module
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import (
    GlossaryEntry,
    ProductPreset,
    SessionContext,
    StartSessionRequest,
)
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


@pytest.fixture
def stores(tmp_path):
    database = str(tmp_path / "project-glossary.db")
    history = SessionRepository(database)
    history.initialize()
    global_terms = GlossaryRepository(database)
    global_terms.initialize()
    scoped_terms = ProjectGlossaryRepository(database)
    scoped_terms.initialize()
    projects = ProjectRepository(database)
    first = projects.create(ProjectInput(title="Missionary conference"))
    second = projects.create(ProjectInput(title="Sunday meeting"))
    return projects, global_terms, scoped_terms, first.project_id, second.project_id


def _term(text: str, source: str = "ko", **kwargs) -> GlossaryEntry:
    return GlossaryEntry(
        term=text, source_language=source, translations={"en": "global"},
        **kwargs,
    )


def test_project_scope_isolated_from_global_and_other_project(stores):
    projects, global_terms, scoped, first, second = stores
    global_terms.create(_term("은혜"))
    one = scoped.create(
        first, GlossaryEntry(
            term="은혜", translations={"en": "grace-for-project"},
            presets=[ProductPreset.CHURCH],
        ),
    )
    assert scoped.list(first)[0].id == one.id
    assert scoped.list(second) == []
    assert global_terms.list()[0].translations["en"] == "global"
    with pytest.raises(ValueError, match="duplicate"):
        scoped.create(first, GlossaryEntry(term="은혜"))
    assert scoped.update(second, one.id, GlossaryEntry(term="not-ours")) is None
    assert scoped.delete(second, one.id) is False
    assert scoped.update(first, one.id, GlossaryEntry(term="은혜", enabled=False))
    assert scoped.list(first)[0].enabled is False
    assert projects.get(first) is not None


def test_project_glossary_import_preserves_ids_and_cascades_project_deletion(stores):
    projects, _, scoped, first, second = stores
    created = scoped.create(first, _term("믿음"))
    assert scoped.import_entries(first, [_term("믿음"), _term("진리")]) == (1, 1, 0)
    assert scoped.import_entries(first, [_term("믿음")], conflict_policy="skip") == (0, 0, 1)
    assert next(x for x in scoped.list(first) if x.term == "믿음").id == created.id
    scoped.create(second, _term("믿음"))
    assert projects.delete(first) is True
    with sqlite3.connect(scoped.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM project_glossary_entries WHERE project_id=?", (first,)
        ).fetchone()[0] == 0
    assert [x.term for x in scoped.list(second)] == ["믿음"]


def test_session_snapshot_project_precedence_and_source_isolation(monkeypatch, stores):
    _, global_terms, scoped, first, second = stores
    global_terms.create(
        GlossaryEntry(term="grace", source_language="en", translations={"ko": "은혜"})
    )
    global_terms.create(_term("은혜"))
    scoped.create(
        first, GlossaryEntry(term="grace", source_language="en", translations={"ko": "풍성한 은혜"})
    )
    scoped.create(first, GlossaryEntry(term="은혜", enabled=False))
    monkeypatch.setattr(main_module, "glossary_repository", global_terms)
    monkeypatch.setattr(main_module, "project_glossary_repository", scoped)

    first_req = StartSessionRequest(
        project_id=first, engine="mock", translation_provider="demo",
        source_language="en", target_languages=["ko"],
        context=SessionContext(preset=ProductPreset.CHURCH),
    )
    snapshot = main_module._with_saved_glossary(first_req)
    assert len(snapshot.context.glossary) == 1
    assert snapshot.context.glossary[0].translations["ko"] == "풍성한 은혜"
    # A concurrent edit cannot mutate a materialized session-start snapshot.
    english = next(x for x in scoped.list(first) if x.source_language == "en")
    scoped.update(first, english.id, GlossaryEntry(
        term="grace", source_language="en", translations={"ko": "수정된 표현"},
    ))
    assert snapshot.context.glossary[0].translations["ko"] == "풍성한 은혜"

    overridden = main_module._with_saved_glossary(first_req.model_copy(update={
        "context": SessionContext(
            preset=ProductPreset.CHURCH,
            glossary=[GlossaryEntry(
                term="grace", source_language="en", translations={"ko": "세션 전용"},
            )],
        ),
    }))
    assert overridden.context.glossary[0].translations["ko"] == "세션 전용"
    other = main_module._with_saved_glossary(first_req.model_copy(update={"project_id": second}))
    assert other.context.glossary[0].translations["ko"] == "은혜"

    korean_req = first_req.model_copy(update={"source_language": "ko", "target_languages": ["en"]})
    korean_context = main_module._with_saved_glossary(korean_req).context
    assert all(entry.term != "은혜" for entry in korean_context.glossary)


def test_project_glossary_api_is_operator_only_and_project_isolated(monkeypatch, stores):
    projects, global_terms, scoped, first, second = stores
    monkeypatch.setattr(main_module, "project_repository", projects)
    monkeypatch.setattr(main_module, "project_glossary_repository", scoped)
    monkeypatch.setattr(main_module, "glossary_repository", global_terms)
    operator = TestClient(main_module.app, client=("127.0.0.1", 40000))
    remote = TestClient(main_module.app, client=("203.0.113.71", 40000))
    uri = f"/api/v1/projects/{first}/glossary"

    assert remote.get(uri).status_code == 403
    assert operator.get("/api/v1/projects/nonexistent/glossary").status_code == 404
    response = operator.post(uri, json=GlossaryEntry(
        term="grace", source_language="en", translations={"ko": "은혜"},
    ).model_dump(mode="json"))
    assert response.status_code == 201
    record_id = response.json()["id"]
    assert operator.get(uri).json()[0]["term"] == "grace"
    assert operator.get(f"/api/v1/projects/{second}/glossary").json() == []
    assert operator.put(f"/api/v1/projects/{second}/glossary/{record_id}",
                        json={"term": "wrong"}).status_code == 404
    assert operator.get(uri + "/export?format=json").status_code == 200
    assert operator.delete(f"{uri}/{record_id}").status_code == 204
    assert operator.get(uri).json() == []
