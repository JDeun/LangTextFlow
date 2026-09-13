from __future__ import annotations

import csv
import io
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .models import GlossaryEntry, GlossaryRecord

MAX_GLOSSARY_IMPORT_ENTRIES = 5000


class GlossaryTransferFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


class GlossaryConflictPolicy(StrEnum):
    UPSERT = "upsert"
    SKIP = "skip"


class GlossaryImportRequest(BaseModel):
    format: GlossaryTransferFormat
    content: str = Field(min_length=1, max_length=2_000_000)
    conflict_policy: GlossaryConflictPolicy = GlossaryConflictPolicy.UPSERT


class GlossaryImportResult(BaseModel):
    total: int = Field(ge=0)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    skipped: int = Field(ge=0)


def export_glossary(
    records: list[GlossaryRecord],
    transfer_format: GlossaryTransferFormat,
) -> str:
    entries = [_portable_entry(record) for record in records]
    if transfer_format is GlossaryTransferFormat.JSON:
        return json.dumps(
            {
                "schema_version": 1,
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
    if transfer_format is GlossaryTransferFormat.CSV:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "term",
                "aliases_json",
                "translations_json",
                "category",
                "presets_json",
                "boost",
                "enabled",
            ],
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "term": _csv_safe_text(entry["term"]),
                    "aliases_json": json.dumps(entry["aliases"], ensure_ascii=False),
                    "translations_json": json.dumps(
                        entry["translations"],
                        ensure_ascii=False,
                    ),
                    "category": _csv_safe_text(entry["category"]),
                    "presets_json": json.dumps(entry["presets"], ensure_ascii=False),
                    "boost": entry["boost"],
                    "enabled": "true" if entry["enabled"] else "false",
                }
            )
        # BOM keeps Korean text readable when the file is opened directly in Excel.
        return "\ufeff" + output.getvalue()
    raise ValueError(f"unsupported glossary export format: {transfer_format}")


def parse_glossary_import(payload: GlossaryImportRequest) -> list[GlossaryEntry]:
    if payload.format is GlossaryTransferFormat.JSON:
        raw_entries = _parse_json(payload.content)
    elif payload.format is GlossaryTransferFormat.CSV:
        raw_entries = _parse_csv(payload.content)
    else:
        raise ValueError(f"unsupported glossary import format: {payload.format}")

    if not raw_entries:
        raise ValueError("glossary import contains no entries")
    if len(raw_entries) > MAX_GLOSSARY_IMPORT_ENTRIES:
        raise ValueError(
            f"glossary import exceeds the {MAX_GLOSSARY_IMPORT_ENTRIES}-entry limit"
        )

    entries: list[GlossaryEntry] = []
    seen_terms: set[str] = set()
    for index, raw in enumerate(raw_entries, start=1):
        normalized = dict(raw)
        term = str(normalized.get("term", "")).strip()
        category = str(normalized.get("category", "general")).strip() or "general"
        normalized["term"] = term
        normalized["category"] = category
        try:
            entry = GlossaryEntry.model_validate(normalized)
        except ValidationError as exc:
            raise ValueError(f"invalid glossary entry at row {index}: {exc}") from exc

        key = entry.term.casefold()
        if key in seen_terms:
            raise ValueError(f"duplicate term in import: {entry.term}")
        seen_terms.add(key)
        entries.append(entry)
    return entries


def glossary_export_media_type(transfer_format: GlossaryTransferFormat) -> str:
    if transfer_format is GlossaryTransferFormat.JSON:
        return "application/json; charset=utf-8"
    return "text/csv; charset=utf-8"


def _portable_entry(record: GlossaryRecord) -> dict[str, Any]:
    return {
        "term": record.term,
        "aliases": list(record.aliases),
        "translations": dict(record.translations),
        "category": record.category,
        "presets": [preset.value for preset in record.presets],
        "boost": record.boost,
        "enabled": record.enabled,
    }


def _csv_formula_risky(value: str) -> bool:
    stripped = value.lstrip(" \t\r\n")
    return bool(stripped) and stripped[0] in {"=", "+", "-", "@"}


def _csv_safe_text(value: str) -> str:
    # Excel/LibreOffice may evaluate formula-looking CSV cells on open. Prefixing
    # an apostrophe makes the value text; the importer removes only prefixes that
    # guard a formula-looking value so normal apostrophes round-trip unchanged.
    return f"'{value}" if _csv_formula_risky(value) else value


def _csv_unescape_text(value: str) -> str:
    if value.startswith("'") and _csv_formula_risky(value[1:]):
        return value[1:]
    return value


def _parse_json(content: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(content.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid glossary JSON: {exc.msg}") from exc

    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict) and isinstance(data.get("entries"), list):
        raw_entries = data["entries"]
    else:
        raise ValueError("glossary JSON must be an entry array or an object with entries[]")

    if not all(isinstance(item, dict) for item in raw_entries):
        raise ValueError("every glossary JSON entry must be an object")
    return [dict(item) for item in raw_entries]


def _parse_csv(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None or "term" not in reader.fieldnames:
        raise ValueError("glossary CSV requires a term column")

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader, start=2):
        term = _csv_unescape_text(row.get("term") or "").strip()
        if not term and not any((value or "").strip() for value in row.values()):
            continue
        if not term:
            raise ValueError(f"glossary CSV row {index} is missing term")
        try:
            raw_category = _csv_unescape_text(row.get("category") or "general")
            rows.append(
                {
                    "term": term,
                    "aliases": _json_cell(row.get("aliases_json"), [], "aliases_json", index),
                    "translations": _json_cell(
                        row.get("translations_json"),
                        {},
                        "translations_json",
                        index,
                    ),
                    "category": raw_category.strip() or "general",
                    "presets": _json_cell(row.get("presets_json"), [], "presets_json", index),
                    "boost": _float_cell(row.get("boost"), 1.0, "boost", index),
                    "enabled": _bool_cell(row.get("enabled"), True, index),
                }
            )
        except ValueError:
            raise
    return rows


def _json_cell(value: str | None, default: Any, field: str, row: int) -> Any:
    if value is None or not value.strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {field} JSON at CSV row {row}") from exc


def _float_cell(value: str | None, default: float, field: str, row: int) -> float:
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field} at CSV row {row}") from exc


def _bool_cell(value: str | None, default: bool, row: int) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid enabled value at CSV row {row}")
