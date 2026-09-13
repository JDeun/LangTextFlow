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
        persistence_error="provider rejected super-secret-api-key at /home/alice/private/db",
    )
    metrics = RealtimeMetrics(audio_frames_received=4, audio_bytes_received=1024)

    payload = build_diagnostics_bundle(
        version="0.12.0",
        settings=settings,
        state=state,
        metrics=metrics,
        preflight={
            "details": {
                "path": "/home/alice/.cache/model",
                "error": (
                    "Authorization Bearer bearer-token-1234567890; "
                    "fallback sk-abcdefghijklmnopqrstuv; "
                    "configured super-secret-api-key"
                ),
            }
        },
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
    assert manifest["privacy"]["contains_user_home_path"] is False
    assert state_json["context"]["hotword_count"] == 1
    assert state_json["context"]["glossary_count"] == 1
    assert state_json["context"]["reference_document_count"] == 1
    assert preflight_json["details"]["path"] == "~/.cache/model"
    assert "<redacted-secret>" in preflight_json["details"]["error"]

    for secret in [
        "super-secret-api-key",
        "bearer-token-1234567890",
        "sk-abcdefghijklmnopqrstuv",
        "ABC234",
        "Private worship service",
        "Alice Example",
        "SecretHotword",
        "SecretDoctrine",
        "Sensitive reference text",
        "private-sermon.md",
        "/home/alice",
    ]:
        assert secret not in combined


def test_diagnostics_redacts_home_paths_from_supported_platforms_and_nested_errors() -> None:
    settings = Settings()
    state = SessionState()
    metrics = RealtimeMetrics()

    payload = build_diagnostics_bundle(
        version="0.12.0",
        settings=settings,
        state=state,
        metrics=metrics,
        preflight={
            "linux": "/home/alice/.cache/model",
            "macos": "/Users/bob/Library/Caches/model",
            "windows": r"C:\Users\carol\AppData\Local\model",
            "wsl": "/mnt/c/Users/dave/.cache/model",
            "nested": [
                {"error": "failed to open /home/erin/private/config.json"},
                {"error": r"failed to open C:\Users\frank\secret\config.json"},
            ],
        },
    )

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        preflight_json = json.loads(archive.read("preflight.json"))
        combined = archive.read("preflight.json").decode("utf-8")

    assert preflight_json["linux"] == "~/.cache/model"
    assert preflight_json["macos"] == "~/Library/Caches/model"
    assert preflight_json["windows"] == r"~\AppData\Local\model"
    assert preflight_json["wsl"] == "~/.cache/model"
    assert preflight_json["nested"][0]["error"] == "failed to open ~/private/config.json"
    assert preflight_json["nested"][1]["error"] == r"failed to open ~\secret\config.json"

    for username in ["alice", "bob", "carol", "dave", "erin", "frank"]:
        assert username not in combined
