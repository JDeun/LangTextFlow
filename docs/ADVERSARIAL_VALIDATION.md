# Adversarial Validation

LangTextFlow treats malformed inputs, hostile local web content, slow clients, provider failures, persistence failures, and concurrency races as first-class release concerns.

## Automated adversarial coverage

### Audio boundary

The backend rejects malformed float32 PCM before expensive VAD/ASR work, including:

- frames that exceed the configured size cap;
- byte lengths that are not float32-aligned;
- NaN and positive/negative infinity;
- implausible sample amplitude.

### Operator HTTP/WebSocket boundary

Tests and runtime guards cover:

- operator REST/WebSocket access restricted to actual loopback socket clients;
- forwarding headers such as `X-Forwarded-For` cannot turn a remote client into a local operator;
- browser `Origin` validation on operator REST and WebSocket requests;
- `Sec-Fetch-Site: cross-site` rejection for operator HTTP requests, including simple cross-origin POSTs that CORS alone would not prevent from executing;
- native/CLI local clients without browser Origin/Fetch-Metadata headers remain supported;
- caption sockets remain server-push-only;
- invalid audio control messages are rejected.

### Audience/WebSocket boundary

Tests and runtime guards cover:

- audience join-code brute-force blocking;
- the rate limiter uses the observed socket client rather than forwarded-address headers;
- invalid browser origins are rejected before consuming a join attempt;
- bounded audience connection counts;
- atomic connection-capacity enforcement under concurrent WebSocket handshakes;
- 128-client fanout regression coverage;
- slow/broken audience clients are isolated by concurrent sends and per-client timeouts rather than blocking healthy clients;
- browser reconnect policy stops on terminal authorization/not-found closes, honors server rate-limit retry timing, and uses bounded exponential backoff with jitter for transient failures;
- malformed caption WebSocket payloads are rejected without crashing the React render path.

### Document boundary

Context-document extraction enforces:

- encoded-payload size rejection before base64 decoding and allocating decoded bytes;
- decoded total file-size limits;
- PDF page limits;
- DOCX ZIP-entry and uncompressed-size limits;
- DOCX compression-ratio limits;
- rejection of encrypted DOCX archives;
- DTD/entity rejection;
- `defusedxml` parsing for Office XML;
- bounded extracted text;
- malformed base64/ZIP corpus rejection.

Image-only/scanned PDFs do not silently invoke OCR.

### Import/export boundary

Portable data paths are treated as untrusted even though they are operator-initiated:

- glossary JSON/CSV content is size-bounded and limited to 5,000 entries per import;
- malformed typed cells and duplicate terms are rejected before repository writes;
- formula-looking glossary CSV text (`=`, `+`, `-`, `@`) is neutralized for spreadsheet applications and reversibly unescaped on LangTextFlow re-import;
- SRT/WebVTT caption text removes NUL/CR variants and consecutive blank cue separators so provider/model output cannot terminate a cue and inject a second subtitle block;
- JSON export remains structured serialization rather than string interpolation.

### Diagnostics/privacy boundary

Support bundles use an allowlisted session/settings shape and a final recursive sanitization pass. Regression tests cover:

- transcript, join code, title/presenter, glossary/hotword terms, raw reference text, and filenames remaining absent;
- Linux, macOS, Windows, and WSL user-home path redaction;
- configured API keys being removed even when echoed inside nested provider/preflight errors;
- common Bearer/OpenAI/GitHub/Slack credential-shaped strings being redacted;
- diagnostic ZIP responses remaining metadata-only and cache-disabled at the API boundary.

### LLM boundary

Transcript, glossary, and reference-document content is serialized as untrusted data and separated from system policy. Correction/translation adapters also bound model response size. LLM failure or unsafe correction output degrades to the deterministic/original path rather than blocking live captions.

### Persistence/failure containment

Failure-injection tests verify that SQLite write failures such as `disk full` are reported as persistence errors without terminating the in-memory live caption path. Runtime lifecycle tests verify persistence-worker cleanup, interrupted-session recovery, and serialization of overlapping session start/stop lifecycle operations.

## Supply-chain and repository gates

CI runs:

- `pip check`;
- `pip-audit` and a Python CycloneDX SBOM artifact;
- `npm audit --audit-level=high` and a frontend CycloneDX SBOM artifact;
- Ruff;
- Bandit medium/high findings gate;
- CodeQL for Python and JavaScript/TypeScript;
- repository secret scanning plus forbidden tracked runtime artifacts, dangerous frontend HTML/eval primitives, and dangerous backend execution/deserialization primitives;
- Hugging Face `snapshot_download()` local-cache policy scanning;
- local Markdown link validation;
- an explicit adversarial boundary/fanout/concurrency regression suite;
- full backend tests with a 70% coverage floor;
- real Uvicorn startup/lifespan `/health` smoke;
- deterministic frontend install (`npm ci`), frontend policy tests, TypeScript check, and production build;
- Windows and macOS boundary smoke runs for diagnostics/network/mDNS/runtime lifecycle plus frontend test/build;
- job-level timeouts and cancellation of superseded CI runs so hung or stale runs cannot consume the release queue indefinitely.

Development dependencies are kept minimal; unused packages are removed rather than retained merely because audits currently pass.

## What automated adversarial tests do not prove

The automated suite cannot prove microphone-driver stability, GPU/runtime stability under prolonged heat/load, Wi-Fi behavior in a crowded venue, room acoustics, real-world ASR quality, translation quality, or 30/60/90-minute memory/latency behavior on target hardware. Those remain physical release-acceptance tests in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## Adding a regression

When an adversarial or reliability bug is found:

1. reproduce it with the smallest deterministic fixture possible;
2. add a regression test that demonstrates the failure;
3. fix the boundary rather than suppressing the test;
4. update this document when the threat model materially changes.
