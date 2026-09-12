# Adversarial Validation

LangTextFlow treats malformed inputs, hostile local web content, slow clients, provider failures, and persistence failures as first-class release concerns.

## Automated adversarial coverage

### Audio boundary

The backend rejects malformed float32 PCM before expensive VAD/ASR work, including:

- frames that exceed the configured size cap;
- byte lengths that are not float32-aligned;
- NaN and positive/negative infinity;
- implausible sample amplitude.

### WebSocket boundary

Tests and runtime guards cover:

- operator sockets restricted to loopback clients;
- browser `Origin` validation to mitigate cross-site WebSocket hijacking;
- caption sockets remaining server-push-only;
- invalid audio control messages;
- audience join-code brute-force blocking;
- bounded audience connection counts;
- slow/broken audience clients isolated by concurrent sends and per-client timeouts.

### Document boundary

Context-document extraction enforces:

- total file-size limits;
- PDF page limits;
- DOCX ZIP-entry and uncompressed-size limits;
- DOCX compression-ratio limits;
- rejection of encrypted DOCX archives;
- DTD/entity rejection;
- `defusedxml` parsing for Office XML;
- bounded extracted text.

Image-only/scanned PDFs do not silently invoke OCR.

### LLM boundary

Transcript, glossary, and reference-document content is serialized as untrusted data and separated from system policy. Correction/translation adapters also bound model response size. LLM failure or unsafe correction output degrades to the deterministic/original path rather than blocking live captions.

### Persistence/failure containment

Failure-injection tests verify that SQLite write failures such as `disk full` are reported as persistence errors without terminating the in-memory live caption path. Runtime lifecycle tests verify persistence-worker cleanup.

## Supply-chain and repository gates

CI runs:

- `pip check`;
- `pip-audit`;
- `npm audit --audit-level=high`;
- Ruff;
- Bandit medium/high findings gate;
- CodeQL for Python and JavaScript/TypeScript;
- repository secret/dangerous-frontend-pattern hygiene scanning;
- Hugging Face `snapshot_download()` local-cache policy scanning;
- local Markdown link validation;
- backend tests with a coverage floor;
- deterministic frontend install (`npm ci`) and production build.

## What automated adversarial tests do not prove

The automated suite cannot prove microphone-driver stability, GPU/runtime stability under prolonged heat/load, Wi-Fi behavior in a crowded venue, room acoustics, real-world ASR quality, translation quality, or 30/60/90-minute memory/latency behavior on target hardware. Those remain physical release-acceptance tests in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## Adding a regression

When an adversarial or reliability bug is found:

1. reproduce it with the smallest deterministic fixture possible;
2. add a regression test that demonstrates the failure;
3. fix the boundary rather than suppressing the test;
4. update this document when the threat model materially changes.
