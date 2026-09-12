from __future__ import annotations

import base64
import io
import zipfile

import pytest
from pypdf import PdfWriter

from langtextflow.context_documents import extract_context_document
from langtextflow.models import ReferenceDocument, SessionContext


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def make_docx(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return output.getvalue()


def test_extracts_utf8_markdown_and_normalizes_whitespace() -> None:
    document = extract_context_document(
        filename="sermon.md",
        content_base64=encoded("# 요한복음\n\n  은혜   와 진리  ".encode()),
    )

    assert document.text == "# 요한복음\n\n은혜 와 진리"
    assert document.character_count == len(document.text)
    assert document.size_bytes > 0
    assert document.truncated is False
    assert len(document.sha256) == 64


def test_extracts_cp949_text() -> None:
    document = extract_context_document(
        filename="notes.txt",
        content_base64=encoded("로마서와 칭의".encode("cp949")),
    )

    assert document.text == "로마서와 칭의"


def test_extracts_docx_paragraphs_without_office_dependency() -> None:
    document = extract_context_document(
        filename="message.docx",
        content_base64=encoded(make_docx("Mission Conference", "John 3:16")),
    )

    assert document.text == "Mission Conference\nJohn 3:16"


def test_rejects_image_only_pdf_without_ocr() -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(output)

    with pytest.raises(ValueError, match="no extractable text"):
        extract_context_document(
            filename="scan.pdf",
            content_base64=encoded(output.getvalue()),
        )


def test_reference_document_extracts_and_excludes_raw_payload_from_snapshot() -> None:
    raw = "설교 제목: 은혜\n본문: 요한복음 3장 16절".encode()
    document = ReferenceDocument(
        filename="sermon.txt",
        media_type="text/plain",
        size_bytes=len(raw),
        content_base64=encoded(raw),
    )

    assert document.text.startswith("설교 제목")
    assert document.content_base64 is None
    dumped = document.model_dump(mode="json")
    assert "content_base64" not in dumped
    assert dumped["sha256"]


def test_session_context_composes_bounded_reference_text() -> None:
    context = SessionContext(
        reference_documents=[
            ReferenceDocument(filename="a.txt", text="첫 번째 문서", size_bytes=10),
            ReferenceDocument(filename="b.md", text="Second document", size_bytes=15),
        ]
    )

    assert "[a.txt]" in context.reference_text
    assert "첫 번째 문서" in context.reference_text
    assert "[b.md]" in context.reference_text
    assert context.reference_excerpt(8) == context.reference_text[:8]
