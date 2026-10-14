"""Project glossary isolation, deterministic session snapshot and cascade."""
from __future__ import annotations

import pytest

from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.models import GlossaryEntry, ProductPreset, SessionContext, StartSessionRequest
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


def test_project_glossary_precedence_snapshot_and_isolation(monkeypatch, tmp_path) -> None:
    db = str(tmp_path / "session.db")
    SessionRepository(db).initialize()
    general = GlossaryRepository(db)
    general.initialize()
    projects = ProjectRepository(db)
    overlay = ProjectGlossaryRepository(db)
    overlay.initialize()
    first = projects.create(ProjectInput(title="Missionary event"))
    second = projects.create(ProjectInput(title="Another event"))
    general.create(GlossaryEntry(
        source_language="en", term="grace", translations={"ko": "은혜"},
        presets=[ProductPreset.CHURCH],
    ))
    general.create(GlossaryEntry(
        source_language="en", term="faith", translations={"ko": "믿음"},
        presets=[ProductPreset.CHURCH],
    ))
    monkeypatch.setattr(main, "glossary_repository", general)
    monkeypatch.setattr(main, "project_glossary_repository", overlay)

    overlay.replace(first.project_id, [
        GlossaryEntry(
            source_language="en", term="grace", translations={"ko": "하나님의 은혜"},
            presets=[ProductPreset.CHURCH],
        ),
        # Project overlay can explicitly disable one inherited term.
        GlossaryEntry(
            source_language="en", term="faith", enabled=False,
            translations={"ko": "믿음"}, presets=[ProductPreset.CHURCH],
        ),
    ])

    def request(project_id: str, terms=None) -> StartSessionRequest:
        return StartSessionRequest(
            project_id=project_id,
            source_language="en", target_languages=["ko"],
            context=SessionContext(
                preset=ProductPreset.CHURCH,
                glossary=terms or [],
            ),
        )

    first_session = main._with_saved_glossary(request(first.project_id))
    assert [(row.term, row.translations["ko"]) for row in first_session.context.glossary] == [
        ("grace", "하나님의 은혜"),
    ]
    second_session = main._with_saved_glossary(request(second.project_id))
    assert {row.term for row in second_session.context.glossary} == {"grace", "faith"}

    overridden_session = main._with_saved_glossary(request(first.project_id, [
        GlossaryEntry(
            source_language="en", term="grace", translations={"ko": "특별한 은혜"},
            presets=[ProductPreset.CHURCH],
        ),
    ]))
    assert overridden_session.context.glossary[0].translations["ko"] == "특별한 은혜"

    overlay.replace(first.project_id, [GlossaryEntry(
        source_language="en", term="grace", translations={"ko": "변경된 용어"},
        presets=[ProductPreset.CHURCH],
    )])
    assert first_session.context.glossary[0].translations["ko"] == "하나님의 은혜"
    assert main._with_saved_glossary(request(first.project_id)).context.glossary[0].translations["ko"] == "변경된 용어"


def test_project_glossary_atomic_invalid_replace_and_delete_cascade(tmp_path) -> None:
    db = str(tmp_path / "session.db")
    SessionRepository(db).initialize()
    projects = ProjectRepository(db)
    overlay = ProjectGlossaryRepository(db)
    overlay.initialize()
    project = projects.create(ProjectInput(title="Archived event"))
    row = GlossaryEntry(term="복음", translations={"en": "gospel"})
    overlay.replace(project.project_id, [row])
    with pytest.raises(ValueError, match="duplicate"):
        overlay.replace(project.project_id, [row, row])
    assert len(overlay.list_for(project.project_id)) == 1
    assert projects.delete(project.project_id) is True
    with pytest.raises(KeyError, match="project not found"):
        overlay.list_for(project.project_id)
    with pytest.raises(KeyError, match="project not found"):
        overlay.replace(project.project_id, [row])
