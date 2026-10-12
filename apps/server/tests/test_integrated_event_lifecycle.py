"""Integrated event lifecycle contract, without a real microphone or cloud LLM.

This checks that project assignment, live PCM recording, committed transcript
search, session-level draft summarization and project deletion preserve their
original foreign-key and file boundaries.
"""
from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from langtextflow.config import Settings
from langtextflow.models import (
    CaptionStage,
    StartSessionRequest,
    TranscriptEvent,
)
from langtextflow.post_event_summary import transcript_source
from langtextflow.project_repository import ProjectInput, ProjectRepository
from langtextflow.runtime import CaptionRuntime


class _FakeStreamingAsr:
    accepts_audio = True
    sample_rate = 16000
    queue_depth = 0
    queue_capacity = 32
    failure = None
    running = False

    async def start(self, request: StartSessionRequest) -> None:
        self.running = True

    async def feed_audio(self, pcm: bytes) -> None:
        assert pcm

    async def end_audio(self) -> None:
        pass

    async def stop(self) -> None:
        self.running = False


@pytest.mark.asyncio
async def test_project_session_recording_transcript_summary_and_retention(
    tmp_path, monkeypatch,
) -> None:
    db = str(tmp_path / "events.db")
    runtime = CaptionRuntime(Settings(database_path=db, storage_reserve_mb=64))
    await runtime.initialize()
    projects = ProjectRepository(db)
    project = projects.create(
        ProjectInput(
            title="통역이 있는 행사", location="서울", status="active",
            event_date="2026-10-12",
        ),
    )
    monkeypatch.setattr(runtime, "_build_engine", lambda _: _FakeStreamingAsr())

    try:
        live = await runtime.start(
            StartSessionRequest(
                project_id=project.project_id,
                record_audio=True,
                source_language="ko",
                target_languages=["en"],
                engine="mock",
                translation_provider="none",
            ),
        )
        assert live.running
        assert live.recording_enabled
        assert live.recording_error is None
        assert runtime.history.get_session(live.session_id).project_id == project.project_id

        # Real float32->PCM16 recording writer and real SQLite history, but
        # a deterministic ASR provider (separate real ASR evaluation is #77).
        await runtime.feed_audio(struct.pack("<f", 0.25) * 320)
        runtime.history.upsert_segment(
            live.session_id,
            TranscriptEvent(
                segment_id="source-1",
                version=1,
                stage=CaptionStage.COMMITTED,
                committed=True,
                text="후속 검토는 금요일까지 하겠습니다.",
                source_language="ko",
                translations={"en": "Review will finish by Friday."},
                start_ms=0,
                end_ms=20,
                emitted_at=datetime.now(UTC),
            ),
        )
        ended = await runtime.stop()
        assert not ended.running
        assert ended.recording_error is None
        assert runtime.history.get_session(live.session_id).ended_at is not None

        recordings = runtime.archive.recordings(live.session_id)
        assert len(recordings) == 1
        recording = recordings[0]
        asset = runtime.archive.recording_path(
            live.session_id, recording["recording_id"],
        )
        assert asset is not None and asset.is_file()
        assert recording["duration_seconds"] == pytest.approx(0.02)

        matches = projects.search_transcripts(project.project_id, "Friday")
        assert len(matches) == 1
        assert matches[0].segment_id == "source-1"
        assert matches[0].original_text.startswith("후속")
        assert matches[0].matched_language == "en"

        source, fingerprint = transcript_source(
            runtime.history.segments(live.session_id),
        )
        assert "후속 검토" in source
        assert "Friday" not in source
        summary = runtime.archive.begin_summary(
            live.session_id,
            language="ko",
            provider="ollama",
            model="local-test",
            transcript_sha256=fingerprint,
            prompt_version="post-event-v1",
        )
        runtime.archive.finish_summary(
            summary["summary_id"], content="후속 검토가 금요일까지 예정되었습니다.",
        )
        assert runtime.archive.latest_summary(live.session_id)["status"] == "completed"

        # Project removal detaches only the grouping, not its original data.
        assert projects.delete(project.project_id)
        assert runtime.history.get_session(live.session_id).project_id is None
        assert runtime.archive.recording_path(live.session_id, recording["recording_id"]) == asset
        assert runtime.archive.latest_summary(live.session_id)["content"].startswith("후속")

        # Explicit session deletion cascades structured history and metadata.
        managed = runtime.archive.recording_paths_for_session(live.session_id)
        assert runtime.history.delete_session(live.session_id)
        for filename in managed:
            filename.unlink(missing_ok=True)
        assert runtime.archive.recordings(live.session_id) == []
        assert runtime.archive.latest_summary(live.session_id) is None
        assert not asset.exists()
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_unknown_project_and_recording_consent_never_stop_running_event(
    tmp_path, monkeypatch,
) -> None:
    db = str(tmp_path / "events.db")
    runtime = CaptionRuntime(Settings(database_path=db))
    await runtime.initialize()
    monkeypatch.setattr(runtime, "_build_engine", lambda _: _FakeStreamingAsr())
    projects = ProjectRepository(db)
    project = projects.create(ProjectInput(title="Active event"))
    request = StartSessionRequest(
        project_id=project.project_id, record_audio=False,
        source_language="ko", target_languages=["en"],
        engine="mock", translation_provider="none",
    )
    try:
        live = await runtime.start(request)
        assert live.running
        assert runtime.archive.recordings(live.session_id) == []
        with pytest.raises(ValueError, match="project not found"):
            await runtime.start(request.model_copy(update={"project_id": "missing"}))
        assert runtime.state.running is True
        assert runtime.state.session_id == live.session_id
    finally:
        await runtime.shutdown()
