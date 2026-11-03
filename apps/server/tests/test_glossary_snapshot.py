"""Review evidence uses the same bounded resolver as start without lifecycle effects."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from langtextflow import main
from langtextflow.glossary_repository import GlossaryRepository
from langtextflow.glossary_snapshot import glossary_snapshot
from langtextflow.models import (
    GlossaryEntry,
    ProductPreset,
    SessionContext,
    SessionState,
    StartSessionRequest,
)
from langtextflow.project_glossary_repository import ProjectGlossaryRepository
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.session_repository import SessionRepository


@pytest.fixture
def stores(tmp_path, monkeypatch):
    db = str(tmp_path / "history.db")
    history = SessionRepository(db)
    history.initialize()
    global_terms, scoped = GlossaryRepository(db), ProjectGlossaryRepository(db)
    global_terms.initialize()
    scoped.initialize()
    project = ProjectRepository(db).create(ProjectInput(title="Synthetic project"))
    monkeypatch.setattr(main, "glossary_repository", global_terms)
    monkeypatch.setattr(main, "project_glossary_repository", scoped)
    monkeypatch.setattr(main.runtime, "start", AsyncMock(return_value=SessionState()))
    monkeypatch.setattr(main, "_close_audio_socket_for_transition", AsyncMock())
    return history, global_terms, scoped, project.project_id


def payload(project_id=None, **updates):
    return {
        "engine": "mock",
        "source_language": "en",
        "target_languages": ["ko"],
        "project_id": project_id,
        "context": {"preset": "church"},
        **updates,
    }


def term(text="grace", translation="은혜", **updates):
    return GlossaryEntry(
        term=text, source_language="en", translations={"ko": translation}, **updates
    )


def test_preview_uses_project_and_session_precedence_without_creating_anything(stores):
    history, global_terms, scoped, project = stores
    global_terms.create(term())
    scoped.create(project, term(translation="프로젝트 은혜"))
    request = payload(
        project,
        context={
            "preset": "church",
            "glossary": [
                term(translation="세션 은혜").model_dump(mode="json"),
            ],
        },
    )
    # Compare logical contents: a read connection may checkpoint existing WAL
    # pages into the main DB file without changing application state.
    with history._connect() as connection:
        before = tuple(connection.iterdump())
    local = TestClient(main.app, client=("127.0.0.1", 40000))
    remote = TestClient(main.app, client=("203.0.113.1", 40000))
    assert remote.post("/api/v1/glossary/resolve", json=request).status_code == 403
    assert local.post("/api/v1/glossary/resolve", json=payload("missing")).status_code == 404
    result = local.post("/api/v1/glossary/resolve", json=request)
    assert result.status_code == 200
    assert result.json()["entries"][0]["translations"] == {"ko": "세션 은혜"}
    assert result.json()["source_language"] == "en" and result.json()["project_id"] == project
    assert history.list_sessions() == []
    main.runtime.start.assert_not_awaited()
    main._close_audio_socket_for_transition.assert_not_awaited()
    # Only database files exist; no WAV, model cache or session artifacts.
    assert not list(history.database_path.parent.rglob("*.wav*"))
    with history._connect() as connection:
        assert tuple(connection.iterdump()) == before


def test_digest_is_canonical_and_binds_scope_and_effective_entries():
    base = StartSessionRequest(**payload())
    first = base.model_copy(
        update={
            "context": SessionContext(
                preset=ProductPreset.CHURCH,
                glossary=[
                    term("mercy"),
                    term(presets=[ProductPreset.LECTURE, ProductPreset.CHURCH]),
                ],
            )
        }
    )
    other = first.model_copy(
        update={
            "context": SessionContext(
                preset=ProductPreset.CHURCH,
                glossary=[
                    term(presets=[ProductPreset.CHURCH, ProductPreset.LECTURE]),
                    term("mercy"),
                ],
            )
        }
    )
    assert glossary_snapshot(first).sha256 == glossary_snapshot(other).sha256
    for update in [
        {"project_id": "different"},
        {"source_language": "ja"},
        {"context": SessionContext(preset=ProductPreset.LECTURE, glossary=first.context.glossary)},
        {
            "context": SessionContext(
                preset=ProductPreset.CHURCH, glossary=[term(translation="변경")]
            )
        },
    ]:
        assert (
            glossary_snapshot(first.model_copy(update=update)).sha256
            != glossary_snapshot(first).sha256
        )


def test_reviewed_snapshot_resolves_all_four_layers_and_reverts(stores):
    _, global_terms, scoped, project = stores
    global_terms.create(term(translation="global"))
    preset = global_terms.create(term(translation="preset", presets=[ProductPreset.CHURCH]))
    overlay = scoped.create(project, term(translation="project"))
    client = TestClient(main.app, client=("127.0.0.1", 40000))
    request = payload(project, context={"preset": "church", "glossary": [
        term(translation="session").model_dump(mode="json"),
    ]})

    def review_and_start(expected):
        reviewed = client.post("/api/v1/glossary/resolve", json=request)
        assert reviewed.status_code == 200
        snapshot = reviewed.json()
        assert len(snapshot["entries"]) == 1
        assert snapshot["entries"][0]["translations"]["ko"] == expected
        started = client.post("/api/v1/session/start", json={
            **request, "glossary_snapshot_sha256": snapshot["sha256"],
        })
        assert started.status_code == 200
        effective = main.runtime.start.await_args.args[0].context.glossary
        assert [entry.model_dump(mode="json") for entry in effective] == snapshot["entries"]
        return snapshot["sha256"]

    digests = [review_and_start("session")]
    request["context"]["glossary"] = []
    digests.append(review_and_start("project"))
    scoped.delete(project, overlay.id)
    digests.append(review_and_start("preset"))
    global_terms.delete(preset.id)
    digests.append(review_and_start("global"))
    assert len(set(digests)) == 4


def test_changed_review_rejects_before_runtime_or_audio_transition_and_legacy_starts(stores):
    _, global_terms, _, _ = stores
    saved = global_terms.create(term())
    client = TestClient(main.app, client=("127.0.0.1", 40000))
    request = payload()
    reviewed = client.post("/api/v1/glossary/resolve", json=request).json()["sha256"]
    global_terms.update(saved.id, term(translation="변경된 용어"))
    result = client.post(
        "/api/v1/session/start", json={**request, "glossary_snapshot_sha256": reviewed}
    )
    assert result.status_code == 409 and "review" in result.json()["detail"]
    main.runtime.start.assert_not_awaited()
    main._close_audio_socket_for_transition.assert_not_awaited()
    assert client.post("/api/v1/session/start", json=request).status_code == 200
    assert (
        main.runtime.start.await_args.args[0].context.glossary[0].translations["ko"]
        == "변경된 용어"
    )
    current = client.post("/api/v1/glossary/resolve", json=request).json()["sha256"]
    assert (
        client.post(
            "/api/v1/session/start", json={**request, "glossary_snapshot_sha256": current}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("bad", ["", "0" * 63, "g" * 64, "A" * 64])
def test_invalid_review_digest_is_rejected(stores, bad):
    client = TestClient(main.app, client=("127.0.0.1", 40000))
    assert (
        client.post("/api/v1/session/start", json=payload(glossary_snapshot_sha256=bad)).status_code
        == 422
    )
    main.runtime.start.assert_not_awaited()


def test_resolved_union_is_validated_before_preview_or_start_and_history_is_immutable(stores):
    history, global_terms, scoped, project = stores
    global_terms.import_entries([term(f"global-{i}") for i in range(251)])
    scoped.replace_snapshot(project, [term(f"project-{i}") for i in range(250)])
    client = TestClient(main.app, client=("127.0.0.1", 40000))
    assert client.post("/api/v1/glossary/resolve", json=payload(project)).status_code == 400
    assert client.post("/api/v1/session/start", json=payload(project)).status_code == 400
    main.runtime.start.assert_not_awaited()
    main._close_audio_socket_for_transition.assert_not_awaited()
    scoped.replace_snapshot(project, [term(f"project-{i}") for i in range(249)])
    result = client.post("/api/v1/glossary/resolve", json=payload(project))
    assert result.status_code == 200 and len(result.json()["entries"]) == 500
    prepared = main._with_saved_glossary(StartSessionRequest(**payload(project)))
    state = SessionState(session_id="reviewed", join_code="TEST", started_at=datetime.now(UTC))
    history.create_session(state, prepared)
    scoped.replace_snapshot(project, [])
    assert len(history.get_session("reviewed").context.glossary) == 500
    assert glossary_snapshot(prepared).sha256 == result.json()["sha256"]
