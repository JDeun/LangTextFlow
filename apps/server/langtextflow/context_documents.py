from __future__ import annotations

import base64
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from defusedxml import ElementTree
from pypdf import PdfReader

MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_CHARS = 60_000
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_COMPRESSION_RATIO = 200.0
MAX_PDF_PAGES = 300

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_SUPPORTED_EXTENSIONS = _TEXT_EXTENSIONS | {".pdf", ".docx"}
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_FORBIDDEN_XML_MARKERS = (b"<!doctype", b"<!entity")


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    size_bytes: int
    character_count: int
    truncated: bool
    sha256: str


def supported_context_document(filename: str) -> bool:
    return Path(filename).suffix.casefold() in _SUPPORTED_EXTENSIONS


def _max_base64_chars(max_bytes: int) -> int:
    # Strict base64 uses four encoded characters for every three decoded bytes.
    # Checking the encoded size before decoding prevents a deliberately enormous
    # request string from causing a second equally large allocation in b64decode().
    return 4 * ((max(0, max_bytes) + 2) // 3)


def extract_context_document(
    *,
    filename: str,
    content_base64: str,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    max_chars: int = MAX_DOCUMENT_CHARS,
) -> ExtractedDocument:
    suffix = Path(filename).suffix.casefold()
    if suffix not in _SUPPORTED_EXTENSIONS:
        raise ValueError("supported context document types are TXT, Markdown, PDF, and DOCX")
    if not content_base64:
        raise ValueError("context document content is empty")
    if max_bytes < 1:
        raise ValueError("context document byte limit must be positive")
    if len(content_base64) > _max_base64_chars(max_bytes):
        raise ValueError("context document encoded payload exceeds the safe size limit")

    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("context document content is not valid base64") from exc

    if not data:
        raise ValueError("context document is empty")
    if len(data) > max_bytes:
        raise ValueError(f"context document exceeds the {max_bytes // (1024 * 1024)} MB limit")

    if suffix in _TEXT_EXTENSIONS:
        raw_text = _extract_plain_text(data)
    elif suffix == ".pdf":
        raw_text = _extract_pdf(data)
    else:
        raw_text = _extract_docx(data)

    normalized = _normalize_text(raw_text)
    if not normalized:
        if suffix == ".pdf":
            raise ValueError(
                "PDF has no extractable text; scanned/image-only PDFs require OCR, "
                "which is not enabled"
            )
        raise ValueError("context document contains no extractable text")

    character_count = len(normalized)
    truncated = character_count > max_chars
    text = normalized[:max_chars].rstrip() if truncated else normalized
    return ExtractedDocument(
        text=text,
        size_bytes=len(data),
        character_count=character_count,
        truncated=truncated,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _extract_plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("text document must be UTF-8 or CP949 encoded")


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise ValueError(f"could not read PDF: {exc}") from exc
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page extraction limit")

    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError(f"could not extract PDF text: {exc}") from exc
        if text.strip():
            pages.append(text)
    return "\n\n".join(pages)


def _validate_docx_archive(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    entries = archive.infolist()
    if len(entries) > MAX_DOCX_ENTRIES:
        raise ValueError("DOCX contains too many ZIP entries")

    total_uncompressed = 0
    for info in entries:
        if info.flag_bits & 0x1:
            raise ValueError("encrypted DOCX files are not supported")
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("DOCX expands beyond the safe uncompressed-size limit")
        if info.file_size:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > MAX_DOCX_COMPRESSION_RATIO:
                raise ValueError("DOCX contains an unsafe compression ratio")

    try:
        document = archive.getinfo("word/document.xml")
    except KeyError as exc:
        raise ValueError("DOCX is missing word/document.xml") from exc
    if document.file_size > MAX_DOCX_XML_BYTES:
        raise ValueError("DOCX document XML is too large to extract safely")
    return document


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = _validate_docx_archive(archive)
            xml_bytes = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX file is not a valid Office document") from exc

    lowered = xml_bytes.lower()
    if any(marker in lowered for marker in _FORBIDDEN_XML_MARKERS):
        raise ValueError("DOCX XML contains forbidden DTD/entity declarations")

    try:
        root = ElementTree.fromstring(xml_bytes)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ValueError("DOCX document XML is malformed or unsafe") from exc

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{_WORD_NS}t")]
        value = "".join(parts).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFC", text).replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    value = "\n".join(lines)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()