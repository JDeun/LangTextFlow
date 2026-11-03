"""Append-only local post-event transcript drafts and operator reviews."""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .models import CaptionStage, TranscriptEvent
from .post_event_archive import _now
from .post_event_summary import MAX_SOURCE_CHARS, transcript_source

MAX_SEGMENTS = 20_000
DECODE_PARAMETERS = {
    "device": "cpu", "compute_type": "int8", "cpu_threads": 2,
    "window_seconds": 30, "beam_size": 5, "condition_on_previous_text": False,
    "vad_filter": False,
}


class TranscriptRequest(BaseModel):
    model_config = {"extra": "forbid"}
    recording_id: str = Field(min_length=1, max_length=36)
    model: Literal["tiny", "base", "small"] = "small"
    language: Literal["ko", "en", "ja"] = "ko"


class TranscriptSegment(BaseModel):
    model_config = {"extra": "forbid"}
    segment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=6000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms < self.start_ms or not self.text.strip():
            raise ValueError("invalid segment interval or empty text")
        return self


class TranscriptReviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    segments: list[TranscriptSegment] = Field(min_length=1, max_length=MAX_SEGMENTS)
    approved: bool = False
    expected_review_id: str | None = Field(default=None, max_length=36)


def validate_segments(segments: list[dict], duration: float) -> list[dict]:
    if not segments or len(segments) > MAX_SEGMENTS:
        raise ValueError("transcript must contain bounded nonempty segments")
    validated = [TranscriptSegment.model_validate(item).model_dump() for item in segments]
    if len({item["segment_id"] for item in validated}) != len(validated):
        raise ValueError("duplicate transcript segment identifier")
    if validated != sorted(validated, key=lambda item: (item["start_ms"], item["segment_id"])):
        raise ValueError("transcript segments must be ordered by time and identifier")
    if any(item["end_ms"] > int(duration * 1000) for item in validated):
        raise ValueError("transcript timestamps exceed the recording")
    if sum(len(item["text"]) + 160 for item in validated) > MAX_SOURCE_CHARS:
        raise ValueError("transcript exceeds bounded evidence size")
    return validated


class TranscriptVersions:
    def __init__(self, archive):
        self.archive = archive

    def initialize(self) -> None:
        with self.archive._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS transcript_versions (
                    transcript_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    recording_id TEXT NOT NULL REFERENCES session_recordings(recording_id)
                        ON DELETE CASCADE,
                    audio_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL, language TEXT NOT NULL,
                    decode_parameters TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK(status IN ('running','completed','error','cancelled')),
                    progress REAL NOT NULL DEFAULT 0,
                    segments TEXT NOT NULL DEFAULT '[]', provenance TEXT NOT NULL DEFAULT '{}',
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_transcript_versions
                    ON transcript_versions(session_id, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_running_transcript
                    ON transcript_versions(session_id) WHERE status='running';
                CREATE TABLE IF NOT EXISTS transcript_reviews (
                    review_id TEXT PRIMARY KEY,
                    transcript_id TEXT NOT NULL REFERENCES transcript_versions(transcript_id)
                        ON DELETE CASCADE,
                    segments TEXT NOT NULL, approved INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_transcript_reviews
                    ON transcript_reviews(transcript_id);
                CREATE TABLE IF NOT EXISTS approved_transcripts (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
                    transcript_id TEXT NOT NULL REFERENCES transcript_versions(transcript_id)
                        ON DELETE CASCADE
                );
            """)
            conn.execute(
                "UPDATE transcript_versions SET status='error', error=?, updated_at=? "
                "WHERE status='running'",
                ("Local transcription interrupted by application restart; retry explicitly",
                 _now()),
            )

    def approved_id(self, session_id: str) -> str | None:
        with self.archive._connect() as conn:
            row = conn.execute(
                "SELECT transcript_id FROM approved_transcripts WHERE session_id=?", (session_id,),
            ).fetchone()
        return row[0] if row else None

    def get(self, session_id: str, transcript_id: str) -> dict | None:
        with self.archive._connect() as conn:
            row = conn.execute(
                "SELECT * FROM transcript_versions WHERE session_id=? AND transcript_id=?",
                (session_id, transcript_id),
            ).fetchone()
            review = conn.execute(
                "SELECT * FROM transcript_reviews WHERE transcript_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (transcript_id,),
            ).fetchone() if row else None
        if row is None:
            return None
        result = dict(row)
        for key in ("segments", "provenance", "decode_parameters"):
            result[key] = json.loads(result[key])
        result["review"] = dict(review) if review else None
        if result["review"]:
            result["review"]["segments"] = json.loads(result["review"]["segments"])
            result["review"]["approved"] = bool(result["review"]["approved"])
        return result

    def list(self, session_id: str) -> list[dict]:
        with self.archive._connect() as conn:
            ids = conn.execute(
                "SELECT transcript_id FROM transcript_versions WHERE session_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 100", (session_id,),
            ).fetchall()
        records = [self.get(session_id, row[0]) for row in ids]
        for record in records:
            record.pop("segments")
            if record["review"]:
                record["review"].pop("segments")
        return records

    def export(self, session_id: str, transcript_id: str) -> dict:
        record = self.get(session_id, transcript_id)
        if record is None:
            raise KeyError("transcript not found")
        with self.archive._connect() as conn:
            reviews = conn.execute(
                "SELECT * FROM transcript_reviews WHERE transcript_id=? ORDER BY rowid",
                (transcript_id,),
            ).fetchall()
        history = []
        for row in reviews:
            review = dict(row)
            review["segments"] = json.loads(review["segments"])
            review["approved"] = bool(review["approved"])
            history.append(review)
        return {"schema_version": 1, "version": record, "reviews": history,
                "approved_transcript_id": self.approved_id(session_id)}

    def begin(self, session_id: str, recording: dict, payload: TranscriptRequest) -> dict:
        transcript_id = str(uuid4())
        stamp = _now()
        with self.archive._connect() as conn:
            conn.execute(
                "INSERT INTO transcript_versions (transcript_id,session_id,recording_id,"
                "audio_sha256,model,language,decode_parameters,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,'running',?,?)",
                (transcript_id, session_id, recording["recording_id"], recording["sha256"],
                 payload.model, payload.language, json.dumps(DECODE_PARAMETERS), stamp, stamp),
            )
        return self.get(session_id, transcript_id)

    def progress(self, transcript_id: str, value: float) -> None:
        with self.archive._connect() as conn:
            conn.execute(
                "UPDATE transcript_versions SET progress=MAX(progress,?),updated_at=? "
                "WHERE transcript_id=? AND status='running'",
                (max(0, min(0.99, value)), _now(), transcript_id),
            )

    def finish(self, transcript_id: str, *, segments=(), provenance=None,
               error: str | None = None, cancelled: bool = False) -> None:
        with self.archive._connect() as conn:
            conn.execute(
                "UPDATE transcript_versions SET status=?,segments=?,provenance=?,error=?,"
                "progress=CASE WHEN ?='completed' THEN 1 ELSE progress END,updated_at=? "
                "WHERE transcript_id=? AND status='running'",
                ("cancelled" if cancelled else "error" if error else "completed",
                 json.dumps(segments, ensure_ascii=False), json.dumps(provenance or {}), error,
                 "error" if error or cancelled else "completed", _now(), transcript_id),
            )

    def review(self, session_id: str, transcript_id: str,
               payload: TranscriptReviewRequest) -> dict:
        with self.archive._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT v.status,r.duration_seconds FROM transcript_versions v "
                "JOIN session_recordings r ON r.recording_id=v.recording_id "
                "WHERE v.session_id=? AND v.transcript_id=?", (session_id, transcript_id),
            ).fetchone()
            if row is None:
                raise KeyError("transcript not found")
            if row["status"] != "completed":
                raise ValueError("only completed transcript drafts can be reviewed")
            if conn.execute(
                "SELECT COUNT(*) FROM transcript_reviews WHERE transcript_id=?", (transcript_id,),
            ).fetchone()[0] >= 100:
                raise ValueError("review limit reached; create a new transcript version")
            segments = validate_segments(
                [item.model_dump() for item in payload.segments], row["duration_seconds"],
            )
            previous = conn.execute(
                "SELECT * FROM transcript_reviews WHERE transcript_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (transcript_id,),
            ).fetchone()
            if previous and previous["approved"]:
                raise ValueError("approved transcript is locked; create a new version")
            if (previous["review_id"] if previous else None) != payload.expected_review_id:
                raise ValueError("transcript review changed; refresh before saving")
            record = {
                "review_id": str(uuid4()), "transcript_id": transcript_id,
                "segments": segments, "approved": payload.approved, "created_at": _now(),
            }
            conn.execute(
                "INSERT INTO transcript_reviews VALUES (?,?,?,?,?)",
                (record["review_id"], transcript_id, json.dumps(segments, ensure_ascii=False),
                 payload.approved, record["created_at"]),
            )
            if payload.approved:
                conn.execute(
                    "INSERT INTO approved_transcripts VALUES (?,?) ON CONFLICT(session_id) "
                    "DO UPDATE SET transcript_id=excluded.transcript_id",
                    (session_id, transcript_id),
                )
        return record

    def source(self, session_id: str, live_segments: list[TranscriptEvent],
               transcript_id: str | None = None) -> tuple[str, str]:
        if transcript_id is None:
            return transcript_source(live_segments)
        record = self.get(session_id, transcript_id)
        if (not record or not record["review"] or not record["review"]["approved"]
                or self.approved_id(session_id) != transcript_id):
            raise ValueError("select the current operator-approved transcript version")
        source, _ = transcript_source([
            TranscriptEvent(**item, version=1, stage=CaptionStage.COMMITTED,
                            committed=True, source_language=record["language"])
            for item in record["review"]["segments"]
        ])
        identity = f"{transcript_id}:{record['review']['review_id']}\n{source}"
        return source, hashlib.sha256(identity.encode()).hexdigest()

    def summary_stale(self, summary: dict, live_segments: list[TranscriptEvent]) -> bool:
        sid = summary["session_id"]
        if summary.get("approved_transcript_id") != self.approved_id(sid):
            return True
        try:
            _, revision = self.source(sid, live_segments, summary.get("transcript_id"))
        except ValueError:
            return True
        return revision != summary["transcript_sha256"]
