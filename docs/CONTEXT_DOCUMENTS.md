# Context Documents

LangTextFlow can attach reference documents to a session so speech recognition and translation have bounded, domain-specific context such as sermon notes, presentation material, speaker names, scripture references, and technical terminology.

## Supported files

- TXT (`.txt`)
- Markdown (`.md`, `.markdown`)
- PDF (`.pdf`)
- DOCX (`.docx`)

The operator UI accepts up to four files per session. Each file is limited to 5 MB.

## Extraction behavior

Text and Markdown are decoded as UTF-8 (including UTF-8 BOM) with CP949 fallback for Korean legacy text files.

PDF text is extracted locally with `pypdf`. A PDF is limited to 300 pages for extraction. Image-only or scanned PDFs with no embedded text are rejected explicitly. OCR is intentionally not run automatically.

DOCX files are treated as Office Open XML containers. LangTextFlow reads `word/document.xml` locally and extracts paragraph text without invoking Microsoft Office or an external service. The uncompressed document XML is capped at 20 MB.

Extracted text is normalized to Unicode NFC, repeated horizontal whitespace is collapsed, and excessive blank lines are reduced.

## Bounds

- maximum documents per session: 4
- maximum source file size: 5 MB per document
- maximum extracted text retained per document: 60,000 characters
- maximum combined `reference_text`: 120,000 characters
- VibeVoice ASR context excerpt: up to 3,000 characters
- faster-whisper initial-prompt excerpt: up to 1,200 characters
- Ollama translation reference excerpt: up to 5,000 characters

These limits intentionally separate archival session context from inference context. A long document can remain represented in the session snapshot without putting the full document into every real-time model request.

## Data lifecycle

The browser reads the selected local file and includes its base64 payload only in the operator's session-start request. `ReferenceDocument` validation extracts the text locally in the LangTextFlow backend, computes SHA-256, and then clears the raw base64 value.

The serialized session state/history stores:

- filename
- media type
- source file size
- extracted text
- original extracted character count
- truncation flag
- SHA-256

The original file bytes/base64 are excluded from the serialized session snapshot and are not stored in SQLite by this feature.

## Runtime use

### VibeVoice

A bounded reference excerpt is appended to `context_info`, alongside title, presenter, description, hotwords, and glossary terms.

### faster-whisper

A smaller bounded reference excerpt is appended to `initial_prompt`. Glossary/hotword handling remains separate through faster-whisper's hotword input.

### Translation

The Ollama translation prompt receives a bounded reference block. The prompt explicitly states that the reference material is only for disambiguating names, terminology, and topic context, and that it must not introduce information that was not spoken.

## Security and privacy notes

Reference documents are operator-side session inputs. They are not added to audience session responses or audience caption WebSocket payloads.

The existing operator API remains loopback-only. Document extraction does not call a cloud OCR, conversion, or document-analysis service.

## Known limitations

- scanned/image-only PDFs require a future explicit OCR workflow
- only primary DOCX body paragraphs are extracted; comments, headers/footers, embedded objects, and complex layout semantics are not interpreted
- context selection is currently prefix/excerpt based rather than semantic retrieval over document chunks

A later improvement can introduce chunking/retrieval for long reference documents if benchmarks show that fixed bounded excerpts are insufficient.
