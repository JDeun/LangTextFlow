"""Fixed private-project package v1 graph; no SQL supplied by an archive."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from uuid import UUID

from .models import GlossaryEntry, SessionContext
from .post_event_summary import MAX_SOURCE_CHARS, SummaryRequest, SummaryReviewRequest
from .post_event_transcripts import TranscriptRequest, validate_segments
from .project_repository import ProjectInput
from .session_repository import SessionRepository
from .summary_evidence import allowed_source_citations

FORMAT = "langtextflow-private-project"
VERSION = 1
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
MAX_GRAPH_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ROWS = 50_000
MAX_SESSIONS = 25
MAX_MEDIA = 64


class PackageError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


# The statements below are complete static SQL. Array order preserves rowid
# precedence for existing append-only reviews and tied generation timestamps.
COLUMNS = {
    "projects": [
        "project_id",
        "title",
        "description",
        "event_date",
        "location",
        "status",
        "created_at",
        "updated_at",
    ],
    "sessions": [
        "session_id",
        "join_code",
        "title",
        "notes",
        "presenter",
        "preset",
        "source_language",
        "target_languages_json",
        "engine",
        "correction_provider",
        "correction_model",
        "translation_provider",
        "translation_model",
        "context_json",
        "started_at",
        "ended_at",
        "interrupted",
    ],
    "project_sessions": ["session_id", "project_id"],
    "transcript_segments": [
        "session_id",
        "segment_id",
        "version",
        "source_revision",
        "stage",
        "source_language",
        "text",
        "original_text",
        "translations_json",
        "translation_states_json",
        "start_ms",
        "end_ms",
        "speaker",
        "confidence",
        "correction_json",
        "asr_provenance_json",
        "translation_provenance_json",
        "committed",
        "emitted_at",
    ],
    "session_recordings": [
        "recording_id",
        "session_id",
        "filename",
        "media_type",
        "size_bytes",
        "sha256",
        "duration_seconds",
        "sample_rate",
        "channels",
        "created_at",
        "status",
        "finalized_at",
    ],
    "transcript_versions": [
        "transcript_id",
        "session_id",
        "recording_id",
        "audio_sha256",
        "model",
        "language",
        "decode_parameters",
        "status",
        "progress",
        "segments",
        "provenance",
        "error",
        "created_at",
        "updated_at",
    ],
    "transcript_reviews": ["review_id", "transcript_id", "segments", "approved", "created_at"],
    "approved_transcripts": ["session_id", "transcript_id"],
    "session_summaries": [
        "summary_id",
        "session_id",
        "language",
        "provider",
        "model",
        "transcript_sha256",
        "prompt_version",
        "status",
        "content",
        "error",
        "created_at",
        "updated_at",
        "transcript_id",
        "approved_transcript_id",
    ],
    "summary_reviews": ["review_id", "summary_id", "content", "approved", "created_at"],
    "project_glossary_entries": [
        "id",
        "project_id",
        "source_language",
        "term_key",
        "entry_json",
        "created_at",
        "updated_at",
    ],
    "project_digests": [
        "digest_id",
        "project_id",
        "input_sha256",
        "snapshot_json",
        "language",
        "provider",
        "model",
        "prompt_version",
        "status",
        "content",
        "error",
        "created_at",
        "updated_at",
    ],
    "project_digest_reviews": ["review_id", "digest_id", "content", "approved", "created_at"],
    "project_digest_sessions": ["digest_id", "session_id"],
}
PRIMARY_KEYS = {
    "projects": ["project_id"],
    "sessions": ["session_id"],
    "project_sessions": ["session_id"],
    "transcript_segments": ["session_id", "segment_id"],
    "session_recordings": ["recording_id"],
    "transcript_versions": ["transcript_id"],
    "transcript_reviews": ["review_id"],
    "approved_transcripts": ["session_id"],
    "session_summaries": ["summary_id"],
    "summary_reviews": ["review_id"],
    "project_glossary_entries": ["id"],
    "project_digests": ["digest_id"],
    "project_digest_reviews": ["review_id"],
    "project_digest_sessions": ["digest_id", "session_id"],
}
EXPORT_SQL = {
    "projects": (
        "SELECT p.project_id,p.title,p.description,p.event_date,p.location,p.status,"
        "p.created_at,p.updated_at"
        " FROM projects p WHERE p.project_id=? ORDER BY p.rowid LIMIT ?"
    ),
    "sessions": (
        "SELECT s.session_id,s.join_code,s.title,s.notes,s.presenter,s.preset,"
        "s.source_language,s.target_languages_json,s.engine,s.correction_provider,"
        "s.correction_model,s.translation_provider,s.translation_model,s.context_json,"
        "s.started_at,s.ended_at,s.interrupted"
        " FROM sessions s JOIN project_sessions p ON p.session_id=s.session_id WHERE "
        "p.project_id=? ORDER BY s.rowid LIMIT ?"
    ),
    "project_sessions": (
        "SELECT p.session_id,p.project_id"
        " FROM project_sessions p WHERE p.project_id=? ORDER BY p.rowid LIMIT ?"
    ),
    "transcript_segments": (
        "SELECT t.session_id,t.segment_id,t.version,t.source_revision,t.stage,"
        "t.source_language,t.text,t.original_text,t.translations_json,"
        "t.translation_states_json,t.start_ms,t.end_ms,t.speaker,t.confidence,"
        "t.correction_json,t.asr_provenance_json,t.translation_provenance_json,t.committed,"
        "t.emitted_at"
        " FROM transcript_segments t JOIN project_sessions p ON p.session_id=t.session_id "
        "WHERE p.project_id=? ORDER BY t.rowid LIMIT ?"
    ),
    "session_recordings": (
        "SELECT r.recording_id,r.session_id,r.filename,r.media_type,r.size_bytes,r.sha256,"
        "r.duration_seconds,r.sample_rate,r.channels,r.created_at,r.status,r.finalized_at"
        " FROM session_recordings r JOIN project_sessions p ON p.session_id=r.session_id WHERE "
        "p.project_id=? ORDER BY r.rowid LIMIT ?"
    ),
    "transcript_versions": (
        "SELECT v.transcript_id,v.session_id,v.recording_id,v.audio_sha256,v.model,v.language,"
        "v.decode_parameters,v.status,v.progress,v.segments,v.provenance,v.error,v.created_at,"
        "v.updated_at"
        " FROM transcript_versions v JOIN project_sessions p ON p.session_id=v.session_id "
        "WHERE p.project_id=? ORDER BY v.rowid LIMIT ?"
    ),
    "transcript_reviews": (
        "SELECT r.review_id,r.transcript_id,r.segments,r.approved,r.created_at"
        " FROM transcript_reviews r JOIN transcript_versions v ON "
        "v.transcript_id=r.transcript_id JOIN project_sessions p ON p.session_id=v.session_id "
        "WHERE p.project_id=? ORDER BY r.rowid LIMIT ?"
    ),
    "approved_transcripts": (
        "SELECT a.session_id,a.transcript_id"
        " FROM approved_transcripts a JOIN project_sessions p ON p.session_id=a.session_id "
        "WHERE p.project_id=? ORDER BY a.rowid LIMIT ?"
    ),
    "session_summaries": (
        "SELECT s.summary_id,s.session_id,s.language,s.provider,s.model,s.transcript_sha256,"
        "s.prompt_version,s.status,s.content,s.error,s.created_at,s.updated_at,s.transcript_id,"
        "s.approved_transcript_id"
        " FROM session_summaries s JOIN project_sessions p ON p.session_id=s.session_id WHERE "
        "p.project_id=? ORDER BY s.rowid LIMIT ?"
    ),
    "summary_reviews": (
        "SELECT r.review_id,r.summary_id,r.content,r.approved,r.created_at"
        " FROM summary_reviews r JOIN session_summaries s ON s.summary_id=r.summary_id JOIN "
        "project_sessions p ON p.session_id=s.session_id WHERE p.project_id=? ORDER BY r.rowid "
        "LIMIT ?"
    ),
    "project_glossary_entries": (
        "SELECT g.id,g.project_id,g.source_language,g.term_key,g.entry_json,g.created_at,"
        "g.updated_at"
        " FROM project_glossary_entries g WHERE g.project_id=? ORDER BY g.rowid LIMIT ?"
    ),
    "project_digests": (
        "SELECT d.digest_id,d.project_id,d.input_sha256,d.snapshot_json,d.language,d.provider,"
        "d.model,d.prompt_version,d.status,d.content,d.error,d.created_at,d.updated_at"
        " FROM project_digests d WHERE d.project_id=? ORDER BY d.rowid LIMIT ?"
    ),
    "project_digest_reviews": (
        "SELECT r.review_id,r.digest_id,r.content,r.approved,r.created_at"
        " FROM project_digest_reviews r JOIN project_digests d ON d.digest_id=r.digest_id "
        "WHERE d.project_id=? ORDER BY r.rowid LIMIT ?"
    ),
    "project_digest_sessions": (
        "SELECT s.digest_id,s.session_id"
        " FROM project_digest_sessions s JOIN project_digests d ON d.digest_id=s.digest_id "
        "WHERE d.project_id=? ORDER BY s.rowid LIMIT ?"
    ),
}
INSERT_SQL = {
    "projects": (
        "INSERT INTO projects (project_id,title,description,event_date,location,status,"
        "created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)"
    ),
    "sessions": (
        "INSERT INTO sessions (session_id,join_code,title,notes,presenter,preset,"
        "source_language,target_languages_json,engine,correction_provider,correction_model,"
        "translation_provider,translation_model,context_json,started_at,ended_at,interrupted)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "project_sessions": ("INSERT INTO project_sessions (session_id,project_id) VALUES (?,?)"),
    "transcript_segments": (
        "INSERT INTO transcript_segments (session_id,segment_id,version,source_revision,stage,"
        "source_language,text,original_text,translations_json,translation_states_json,start_ms,"
        "end_ms,speaker,confidence,correction_json,asr_provenance_json,"
        "translation_provenance_json,committed,emitted_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "session_recordings": (
        "INSERT INTO session_recordings (recording_id,session_id,filename,media_type,"
        "size_bytes,sha256,duration_seconds,sample_rate,channels,created_at,status,"
        "finalized_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "transcript_versions": (
        "INSERT INTO transcript_versions (transcript_id,session_id,recording_id,audio_sha256,"
        "model,language,decode_parameters,status,progress,segments,provenance,error,created_at,"
        "updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "transcript_reviews": (
        "INSERT INTO transcript_reviews (review_id,transcript_id,segments,approved,created_at)"
        " VALUES (?,?,?,?,?)"
    ),
    "approved_transcripts": (
        "INSERT INTO approved_transcripts (session_id,transcript_id) VALUES (?,?)"
    ),
    "session_summaries": (
        "INSERT INTO session_summaries (summary_id,session_id,language,provider,model,"
        "transcript_sha256,prompt_version,status,content,error,created_at,updated_at,"
        "transcript_id,approved_transcript_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "summary_reviews": (
        "INSERT INTO summary_reviews (review_id,summary_id,content,approved,created_at)"
        " VALUES (?,?,?,?,?)"
    ),
    "project_glossary_entries": (
        "INSERT INTO project_glossary_entries (id,project_id,source_language,term_key,"
        "entry_json,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?)"
    ),
    "project_digests": (
        "INSERT INTO project_digests (digest_id,project_id,input_sha256,snapshot_json,language,"
        "provider,model,prompt_version,status,content,error,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "project_digest_reviews": (
        "INSERT INTO project_digest_reviews (review_id,digest_id,content,approved,created_at)"
        " VALUES (?,?,?,?,?)"
    ),
    "project_digest_sessions": (
        "INSERT INTO project_digest_sessions (digest_id,session_id) VALUES (?,?)"
    ),
}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def parse_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackageError("Duplicate JSON keys are not supported")
            result[key] = value
        return result

    def invalid(_):
        raise PackageError("Nonfinite JSON values are not supported")

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PackageError("Invalid private project JSON") from exc


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise PackageError("Invalid project graph identifier")
    return value


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise PackageError("Invalid project content digest")


def _date(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or len(value) > 64:
        raise PackageError("Invalid project timestamp")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise PackageError("Invalid project timestamp") from exc


def _flag(value):
    if type(value) is not int or value not in (0, 1):
        raise PackageError("Invalid project boolean")


def validate_graph(graph):
    """Validate the closed graph before any destination mutation or file promotion."""
    if not isinstance(graph, dict) or set(graph) != {"project_id", "tables"}:
        raise PackageError("Unsupported private project graph")
    pid = identifier(graph["project_id"])
    tables = graph["tables"]
    if not isinstance(tables, dict) or set(tables) != set(COLUMNS):
        raise PackageError("Unsupported project table set")
    total = 0
    indices = {}
    for table, columns in COLUMNS.items():
        rows = tables[table]
        if not isinstance(rows, list):
            raise PackageError("Invalid project table rows")
        total += len(rows)
        if total > MAX_ROWS:
            raise PackageError("Project package exceeds 50000 rows", 413)
        expected = set(columns) - ({"filename"} if table == "session_recordings" else set())
        seen = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected:
                raise PackageError("Unexpected project graph columns")
            if any(type(value) not in (str, int, float, type(None)) for value in row.values()):
                raise PackageError("Invalid project scalar value")
            if any(isinstance(v, float) and not math.isfinite(v) for v in row.values()):
                raise PackageError("Nonfinite project scalar value")
            if any(type(v) is int and not -(2**63) <= v < 2**63 for v in row.values()):
                raise PackageError("Project integer exceeds SQLite's supported range")
            key = tuple(identifier(row[column]) for column in PRIMARY_KEYS[table])
            if key in seen:
                raise PackageError("Duplicate project graph identity")
            seen[key] = row
            for column in ("created_at", "updated_at", "started_at", "emitted_at"):
                if column in row:
                    _date(row[column])
            for column in ("ended_at", "finalized_at"):
                if column in row:
                    _date(row[column], optional=True)
        indices[table] = seen
    if len(tables["projects"]) != 1 or tables["projects"][0]["project_id"] != pid:
        raise PackageError("Package must contain exactly its declared project")
    ProjectInput.model_validate(tables["projects"][0])
    if len(tables["sessions"]) > MAX_SESSIONS or len(tables["session_recordings"]) > MAX_MEDIA:
        raise PackageError("Package exceeds 25 sessions or 64 recordings", 413)
    if len(tables["project_glossary_entries"]) > 500:
        raise PackageError("Project glossary exceeds 500 entries", 413)
    sessions = {row["session_id"]: row for row in tables["sessions"]}
    recordings = {row["recording_id"]: row for row in tables["session_recordings"]}
    versions = {row["transcript_id"]: row for row in tables["transcript_versions"]}
    summaries = {row["summary_id"]: row for row in tables["session_summaries"]}
    digests = {row["digest_id"]: row for row in tables["project_digests"]}
    if {row["session_id"] for row in tables["project_sessions"]} != set(sessions):
        raise PackageError("Project session linkage is incomplete")
    for rows in tables.values():
        for row in rows:
            if "project_id" in row and row["project_id"] != pid:
                raise PackageError("Graph refers to another project")
            if "session_id" in row and row["session_id"] not in sessions:
                raise PackageError("Graph refers to an absent session")
    for row in tables["sessions"]:
        if row["ended_at"] is None:
            raise PackageError("Finish every project session before backup or restore", 409)
        _flag(row["interrupted"])
        if not isinstance(row["join_code"], str) or not re.fullmatch(
            r"[A-Za-z0-9]{6}", row["join_code"]
        ):
            raise PackageError("Invalid inactive historical audience code")
        if (
            not isinstance(row["title"], str)
            or not 1 <= len(row["title"]) <= 120
            or not isinstance(row["notes"], str)
            or len(row["notes"]) > 8000
        ):
            raise PackageError("Invalid historical session metadata")
        SessionContext.model_validate_json(row["context_json"])
        targets = parse_json(row["target_languages_json"])
        if (
            row["source_language"] not in {"ko", "en", "ja"}
            or not isinstance(targets, list)
            or any(
                target not in {"ko", "en", "ja"} or target == row["source_language"]
                for target in targets
            )
        ):
            raise PackageError("Invalid historical language routing")
    for row in tables["transcript_segments"]:
        _flag(row["committed"])
        SessionRepository._segment(row)
    from .recording_journal import RecordingJournal

    for row in recordings.values():
        try:
            if str(UUID(row["recording_id"])) != row["recording_id"]:
                raise ValueError("noncanonical UUID")
        except (ValueError, TypeError, AttributeError) as exc:
            raise PackageError("Invalid portable recording identity") from exc
        RecordingJournal._check_metadata({**row, "filename": "0" * 32 + ".wav"})
        digest(row["sha256"])
        if row["media_type"] != "audio/wav" or row["status"] not in {"completed", "unknown"}:
            raise PackageError("Unsupported recording metadata")
    for row in versions.values():
        recording = recordings.get(row["recording_id"])
        if (
            recording is None
            or recording["session_id"] != row["session_id"]
            or row["audio_sha256"] != recording["sha256"]
        ):
            raise PackageError("Transcript recording reference leaves the project")
        TranscriptRequest(
            recording_id=row["recording_id"], model=row["model"], language=row["language"]
        )
        if row["status"] not in {"completed", "error", "cancelled"}:
            raise PackageError("Wait for transcript jobs to finish before backup", 409)
        if not isinstance(row["progress"], (float, int)) or not 0 <= row["progress"] <= 1:
            raise PackageError("Invalid transcript progress")
        segments = parse_json(row["segments"])
        if row["status"] == "completed" or segments:
            validate_segments(segments, recording["duration_seconds"])
        if not isinstance(parse_json(row["provenance"]), dict) or not isinstance(
            parse_json(row["decode_parameters"]), dict
        ):
            raise PackageError("Invalid transcript provenance")
    latest_transcript_reviews = {}
    for row in tables["transcript_reviews"]:
        version = versions.get(row["transcript_id"])
        if version is None:
            raise PackageError("Review refers to an absent transcript")
        _flag(row["approved"])
        validate_segments(
            parse_json(row["segments"]), recordings[version["recording_id"]]["duration_seconds"]
        )
        latest_transcript_reviews[row["transcript_id"]] = row
    for row in tables["approved_transcripts"]:
        version = versions.get(row["transcript_id"])
        review = latest_transcript_reviews.get(row["transcript_id"])
        if (
            version is None
            or version["session_id"] != row["session_id"]
            or not review
            or not review["approved"]
        ):
            raise PackageError("Invalid approved transcript selection")
    for row in summaries.values():
        SummaryRequest(language=row["language"], provider=row["provider"], model=row["model"])
        digest(row["transcript_sha256"])
        if (
            row["status"] not in {"completed", "error"}
            or not isinstance(row["content"], str)
            or len(row["content"]) > 15000
        ):
            raise PackageError("Wait for summary jobs to finish before backup", 409)
        for key in ("transcript_id", "approved_transcript_id"):
            if row[key] is not None and (
                row[key] not in versions or versions[row[key]]["session_id"] != row["session_id"]
            ):
                raise PackageError("Summary source reference leaves the project")
            if row[key] is not None and (
                versions[row[key]]["status"] != "completed"
                or not latest_transcript_reviews.get(row[key], {}).get("approved")
            ):
                raise PackageError("Summary refers to an unapproved transcript")
    for row in tables["summary_reviews"]:
        if row["summary_id"] not in summaries:
            raise PackageError("Review refers to an absent summary")
        _flag(row["approved"])
        SummaryReviewRequest(content=row["content"], approved=bool(row["approved"]))
    for row in tables["project_glossary_entries"]:
        entry = GlossaryEntry.model_validate_json(row["entry_json"])
        if (
            row["source_language"] != entry.source_language
            or row["term_key"] != entry.term.strip().casefold()
        ):
            raise PackageError("Project glossary identity does not match its entry")
    _validate_project_digests(tables, pid, sessions, versions, summaries, digests)
    return graph


def _validate_project_digests(tables, pid, sessions, versions, summaries, digests):
    from .project_digests import validate_project_citations

    review_ids = {row["review_id"]: row for row in tables["summary_reviews"]}
    live_ids = {
        (row["session_id"], row["segment_id"])
        for row in tables["transcript_segments"]
        if row["committed"]
    }
    offline_ids = {}
    for review in tables["transcript_reviews"]:
        if review["approved"]:
            offline_ids[review["transcript_id"]] = {
                item["segment_id"] for item in parse_json(review["segments"])
            }
    dependencies = {key: set() for key in digests}
    for row in tables["project_digest_sessions"]:
        if row["digest_id"] not in digests:
            raise PackageError("Dependency refers to an absent project digest")
        dependencies[row["digest_id"]].add(row["session_id"])
    for row in digests.values():
        SummaryRequest(language=row["language"], provider=row["provider"], model=row["model"])
        if row["status"] not in {"completed", "error"}:
            raise PackageError("Wait for project jobs to finish before backup", 409)
        snapshot = parse_json(row["snapshot_json"])
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "project",
            "sessions",
            "excluded_running_session_ids",
            "source",
            "notes",
            "citations",
            "input_sha256",
        }:
            raise PackageError("Unsupported project digest snapshot")
        if (
            not isinstance(snapshot["sessions"], list)
            or not 1 <= len(snapshot["sessions"]) <= 25
            or not isinstance(snapshot["citations"], list)
            or len(snapshot["citations"]) > 6000
            or not isinstance(snapshot["source"], str)
            or not isinstance(snapshot["notes"], str)
            or len(snapshot["source"]) + len(snapshot["notes"]) > MAX_SOURCE_CHARS
        ):
            raise PackageError("Project digest snapshot exceeds its original bounds")
        claimed = snapshot["input_sha256"]
        digest(claimed)
        actual = hashlib.sha256(
            canonical({k: v for k, v in snapshot.items() if k != "input_sha256"}).encode()
        ).hexdigest()
        if (
            claimed != row["input_sha256"]
            or claimed != actual
            or snapshot["project"]["project_id"] != pid
        ):
            raise PackageError("Project digest snapshot hash changed")
        scoped = {entry["session_id"] for entry in snapshot["sessions"]}
        if len(scoped) != len(snapshot["sessions"]):
            raise PackageError("Duplicate project digest source session")
        if (
            not scoped <= set(sessions)
            or dependencies[row["digest_id"]] != scoped
            or not set(snapshot["excluded_running_session_ids"]) <= set(sessions)
        ):
            raise PackageError("Historical digest references sessions outside this package", 409)
        entries = {entry["session_id"]: entry for entry in snapshot["sessions"]}
        for entry in entries.values():
            sid = entry["session_id"]
            summary = summaries.get(entry["summary"]["summary_id"])
            if summary is None or summary["session_id"] != sid:
                raise PackageError("Project digest summary reference leaves the package")
            for field in ("transcript_id", "approved_transcript_id"):
                value = entry[field]
                if value is not None and (
                    value not in versions or versions[value]["session_id"] != sid
                ):
                    raise PackageError("Project digest source reference leaves the package")
            if entry["review"]:
                review = review_ids.get(entry["review"]["review_id"])
                if review is None or review["summary_id"] != summary["summary_id"]:
                    raise PackageError("Project digest review reference leaves the package")
        markers = set()
        for reference in snapshot["citations"]:
            entry = entries.get(reference["session_id"])
            if entry is None or reference["transcript_id"] != entry["transcript_id"]:
                raise PackageError("Project citation reference leaves the package")
            if reference["transcript_id"] is None:
                valid = (reference["session_id"], reference["segment_id"]) in live_ids
            else:
                valid = reference["segment_id"] in offline_ids.get(
                    reference["transcript_id"], set()
                )
            if not valid or reference["marker"] in markers:
                raise PackageError("Project citation segment leaves the package")
            if (
                not isinstance(reference["start_ms"], int)
                or reference["start_ms"] < 0
                or reference["marker"]
                != f"[{reference['alias']} at {reference['start_ms'] // 1000}s]"
            ):
                raise PackageError("Project citation time or alias changed")
            markers.add(reference["marker"])
        if markers != allowed_source_citations(snapshot["source"]):
            raise PackageError("Project source citation map is incomplete")
        if row["status"] == "completed":
            validate_project_citations(snapshot, row["content"])
    for row in tables["project_digest_reviews"]:
        original = digests.get(row["digest_id"])
        if original is None or original["status"] != "completed":
            raise PackageError("Review refers to an absent completed project digest")
        _flag(row["approved"])
        SummaryReviewRequest(content=row["content"], approved=bool(row["approved"]))
        validate_project_citations(parse_json(original["snapshot_json"]), row["content"])
