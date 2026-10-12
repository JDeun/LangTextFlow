"""Cross-session project search: exact evidence, scoping, paging, and access control."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from langtextflow.models import (
    CaptionStage,
    TranscriptEvent,
)
from langtextflow.project_repository import ProjectInput

from test_event_projects import _history, _session


def _commit(history, session_id: str, *, text: str, translation: str = "") -> None:
    history.upsert_segment(
        session_id,
        TranscriptEvent(
            segment_id="original", version=2, stage=CaptionStage.COMMITTED,
            source_language="ko", text=text, translations={"en": translation},
            start_ms=2500, end_ms=6000, speaker="Speaker A",
            committed=True,
        ),
    )


def test_project_transcript_search_scopes_text_and_translations(tmp_path) -> None:
    history, repo = _history(tmp_path)
    first = repo.create(ProjectInput(title="AI Conference"))
    other = repo.create(ProjectInput(title="Private event"))
    _session(history, "a", first.project_id)
    _session(history, "b", first.project_id)
    _session(history, "c", other.project_id)
    _commit(history, "a", text="차세대 AI 연구를 소개했습니다.", translation="new AI research")
    _commit(history, "b", text="산업 현장의 응용 사례입니다.", translation="industrial AI")
    _commit(history, "c", text="비공개 AI 연구 발표입니다.", translation="private AI")

    hits = repo.search_transcripts(first.project_id, "AI")
    assert {hit.session_id for hit in hits} == {"a", "b"}
    assert all(hit.session_id != "c" for hit in hits)
    assert all(hit.project_id == first.project_id for hit in hits)
    assert all(hit.segment_id == "original" for hit in hits)
    assert all(hit.start_ms == 2500 for hit in hits)
    assert all(hit.speaker == "Speaker A" for hit in hits)
    assert len(repo.search_transcripts(first.project_id, "AI", limit=1, offset=1)) == 1

    translated = repo.search_transcripts(first.project_id, "industrial")
    assert len(translated) == 1
    assert translated[0].session_id == "b"
    assert translated[0].matched_language == "en"
    assert translated[0].matched_text == "industrial AI"
    assert translated[0].original_text == "산업 현장의 응용 사례입니다."

    original = repo.search_transcripts(first.project_id, "연구")
    assert len(original) == 1
    assert original[0].session_id == "a"
    assert original[0].matched_language == "ko"

    # Search must not match translation JSON keys alone (e.g. key "en").
    assert repo.search_transcripts(first.project_id, "en") == []


def test_literal_search_special_chars_cannot_expand_results(tmp_path) -> None:
    history, repo = _history(tmp_path)
    project = repo.create(ProjectInput(title="Safe Search"))
    _session(history, "literal", project.project_id)
    _commit(history, "literal", text="실험 진행률은 50%입니다. 식별자: abc_def")
    assert len(repo.search_transcripts(project.project_id, "50%")) == 1
    assert len(repo.search_transcripts(project.project_id, "abc_def")) == 1
    assert repo.search_transcripts(project.project_id, "%' OR 1=1--") == []
    assert repo.search_transcripts(project.project_id, "없는 문구") == []
    for invalid in (" ", "a", "x" * 129):
        with pytest.raises(ValueError):
            repo.search_transcripts(project.project_id, invalid)
    with pytest.raises(KeyError, match="project not found"):
        repo.search_transcripts("unknown", "AI")


@pytest.mark.asyncio
async def test_project_search_api_rejects_remote_operator(tmp_path, monkeypatch) -> None:
    from langtextflow import main

    history, repo = _history(tmp_path)
    monkeypatch.setattr(main, "project_repository", repo)
    project = repo.create(ProjectInput(title="Restricted"))
    _session(history, "one", project.project_id)
    _commit(history, "one", text="통합 지식 검색입니다.")
    local = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    remote = SimpleNamespace(client=SimpleNamespace(host="192.168.0.20"), headers={})

    hits = await main.search_project_transcripts(
        local, project.project_id, q="지식", limit=10, offset=0,
    )
    assert len(hits) == 1
    assert hits[0].segment_id == "original"
    with pytest.raises(HTTPException) as error:
        await main.search_project_transcripts(
            remote, project.project_id, q="지식", limit=10, offset=0,
        )
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await main.search_project_transcripts(
            local, "missing", q="지식", limit=10, offset=0,
        )
    assert missing.value.status_code == 404
