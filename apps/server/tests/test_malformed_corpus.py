from __future__ import annotations

import base64
import io
import zipfile

import pytest

from langtextflow.context_documents import MAX_DOCX_ENTRIES, extract_context_document
from langtextflow.telemetry import decode_pcm_f32le


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x00",
        b"\x00\x00",
        b"\x00\x00\x00",
        b"\xff",
        b"\x00" * 5,
        b"\x00" * 7,
    ],
)
def test_pcm_malformed_alignment_corpus_is_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        decode_pcm_f32le(payload)


@pytest.mark.parametrize(
    "encoded",
    [
        "!",
        "!!!!",
        "not-base64",
        "YWJjZA=",
        "YWJjZA===",
    ],
)
def test_context_document_invalid_base64_corpus_is_rejected(encoded: str) -> None:
    with pytest.raises(ValueError):
        extract_context_document(filename="context.txt", content_base64=encoded)


def test_docx_rejects_zip_entry_flood_before_xml_parse() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<document />")
        for index in range(MAX_DOCX_ENTRIES):
            archive.writestr(f"word/noise/{index}.txt", "x")

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    with pytest.raises(ValueError, match="too many ZIP entries"):
        extract_context_document(filename="entry-flood.docx", content_base64=encoded)


def test_docx_rejects_non_zip_payload() -> None:
    encoded = base64.b64encode(b"this is not a zip archive").decode("ascii")
    with pytest.raises(ValueError, match="not a valid Office document"):
        extract_context_document(filename="broken.docx", content_base64=encoded)
