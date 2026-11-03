# Security Model

LangTextFlow is a local-first realtime caption system. Its security model assumes a trusted operator machine and potentially untrusted audience clients, uploaded context, model servers, model output, LAN peers, and web pages open in the operator's browser.

## Trust boundaries

### Trusted operator boundary

The following capabilities are intentionally loopback-only:

- session start/stop;
- audio ingestion;
- glossary mutation/import/export;
- history mutation/deletion;
- model setup and sidecar lifecycle;
- preflight and diagnostics.

Operator REST access is authorized from the actual socket client address; forwarding headers are not trusted. Browser requests are additionally checked against the configured local/LAN Origin policy and `Sec-Fetch-Site: cross-site` is rejected. This prevents an unrelated web page open on the operator PC from using the browser as a confused deputy against `localhost`, including simple cross-origin POST requests whose execution is not prevented by CORS alone.

Operator WebSockets use the same loopback and Origin trust boundary. Native/CLI local clients without browser `Origin`/Fetch-Metadata headers remain supported.

Reverse proxies must not expose operator endpoints to the LAN or public Internet without adding a separate authentication layer and intentionally redefining the trusted-origin policy.

### Audience boundary

Audience caption REST/WebSocket endpoints are read-only. The sole optional POST is numeric display-timing telemetry: it requires the active join code, explicit per-session operator opt-in, audience consent, an allowed browser origin, bounded body/time and rate limits. It cannot edit captions or operator state. A join code acts as a bearer capability for the active session. Invalid-code attempts are rate limited per observed socket client address. Forwarding headers are not trusted for these decisions.

Join codes are appropriate for same-event LAN access, not for protecting confidential broadcasts on an untrusted public network.

### Untrusted documents and text

Treat all of the following as untrusted data:

- TXT/Markdown/PDF/DOCX reference documents;
- session titles and presenter names;
- glossary terms, aliases, and translations;
- transcripts from ASR providers;
- reference context supplied to correction/translation models.

Document extraction enforces byte, page, ZIP-entry, decompressed-size, compression-ratio, and extracted-character limits. DOCX DTD/entity declarations are rejected. Scanned PDFs are not OCRed automatically.

### Model boundary

VibeVoice, Ollama, LM Studio/vLLM-compatible endpoints, and future model providers are external execution domains. LangTextFlow:

- applies request/response size bounds where supported;
- treats model output as untrusted;
- separates system policy from user-controlled context for LLM prompts;
- validates constrained correction output before use;
- degrades to original/deterministic captions when correction or translation fails;
- keeps API credentials on the backend only.

A compromised model server can still return incorrect content. LangTextFlow's model-output checks reduce blast radius but are not a substitute for trusted model artifacts and hosts.

Ollama/OpenAI-compatible translation, constrained correction, and post-event summary
HTTP responses are streamed before JSON parsing. The entire envelope, including
ignored fields and model-list metadata, is limited to 665,536 bytes (the largest
50,000-character output with worst-case JSON escaping plus 64 KiB of envelope room).
`Content-Length` can reject a response early, and actual received bytes are always
counted even when that header is absent or false. Existing extracted-text character
limits still apply afterward. Oversized responses fail the provider operation;
correction retains its deterministic/source fallback, translations remain unavailable,
and summary jobs store a safe error without replacing the source transcript.

These requests advertise `Accept-Encoding: identity` and reject a compressed
response before reading it, preventing decompression from allocating an oversized
body ahead of the byte check. Providers/proxies must honor identity encoding;
automatic gzip/Brotli decompression is intentionally unsupported on these paths.
Redirects are not followed, and HTTP status failures expose only the status code,
without provider URLs, query strings, credentials, or response bodies.
The same bound covers preflight model/configuration probes and VibeVoice HTTP
configuration/health responses. Ollama model-download progress uses a separate
NDJSON streaming protocol; this JSON response cap does not bound its progress lines.

## Availability controls

- bounded ASR, post-processing, and persistence queues;
- audio frame size and float-sanity validation;
- WebSocket broadcast timeouts so one slow audience client cannot block all clients;
- audience connection capacity;
- provider health/failure telemetry;
- VibeVoice to faster-whisper failover in `auto` mode;
- persistence failures degrade without terminating live in-memory captions;
- benchmark/soak harnesses for latency, queues, failover, and memory.

## Stored data

SQLite may contain:

- session metadata;
- extracted reference text and file metadata/hash;
- transcripts and translations;
- glossary data;
- correction provenance.

Original uploaded reference-file bytes are discarded after extraction and are excluded from the persisted session context.

Before distributing a desktop release, the installer must document the database/model-cache locations and provide an explicit deletion/retention workflow.

## Out of scope / residual risks

- physical compromise of the operator machine;
- malicious code in separately installed model runtimes or model artifacts;
- confidentiality against a LAN attacker who obtains the join code;
- semantic hallucination that passes structural/safety checks;
- OS/GPU driver vulnerabilities;
- public-Internet deployment without an additional authenticated reverse proxy.

## Release security gate

A release candidate must satisfy `docs/RELEASE_CHECKLIST.md`. Any unresolved critical/high dependency advisory, exposed secret, operator endpoint reachable from a non-loopback or disallowed browser origin, or reproducible crash/data-loss defect blocks release.
