from __future__ import annotations

import io
import json
import zipfile

from langtextflow.config import Settings
from langtextflow.diagnostics import build_diagnostics_bundle
from langtextflow.models import GlossaryEntry, ReferenceDocument, SessionContext, SessionState
from langtextflow.telemetry import RealtimeMetrics


def test_diagnostics_bundle_excludes_sensitive_session_content() -> None:
    settings = Settings(
        openai_compatible_api_key="super-secret-api-key",
        database_path="/home/alice/private/langtextflow.db",
    )
    context = SessionContext(
        title="Private worship service",
        presenter="Alice Example",
        hotwords=["SecretHotword"],
        glossary=[GlossaryEntry(term="SecretDoctrine")],
        reference_documents=[
            ReferenceDocument(
                filename="private-sermon.md",
                media_type="text/markdown",
                size_bytes=24,
                text="Sensitive reference text",
                sha256="a" * 64,
            )
        ],
    )
    state = SessionState(
        session_id="private-session-id",
        join_code="ABC234",
        running=True,
        source_language="ko",
        target_languages=["en"],
        engine="mock",
        context=context,
    )
    metrics = RealtimeMetrics(audio_frames_received=4, audio_bytes_received=1024)

    payload = build_diagnostics_bundle(
        version="0.12.0",
        settings=settings,
        state=state,
        metrics=metrics,
        preflight={"details": {"path": "/home/alice/.cache/model"}},
        mdns_state={"hostname": "langtextflow-123abc.local", "ready": True},
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert names == {
            "manifest.json",
            "system.json",
            "settings.json",
            "session-state.json",
            "metrics.json",
            "preflight.json",
            "mdns.json",
        }
        combined = "\n".join(
            archive.read(name).decode("utf-8") for name in sorted(names)
        )
        manifest = json.loads(archive.read("manifest.json"))
        state_json = json.loads(archive.read("session-state.json"))
        preflight_json = json.loads(archive.read("preflight.json"))

    assert manifest["version"] == "0.12.0"
    assert manifest["privacy"]["contains_transcript_text"] is False
    assert state_json["context"]["hotword_count"] == 1
    assert state_json["context"]["glossary_count"] == 1
    assert state_json["context"]["reference_document_count"] == 1
    assert preflight_json["details"]["path"].startswith("~")

    for secret in [
        "super-secret-api-key",
        "ABC234",
        "Private worship service",
        "Alice Example",
        "SecretHotword",
        "SecretDoctrine",
        "Sensitive reference text",
        "private-sermon.md",
    ]:
        assert secret not in combined
