# Security Model

LangTextFlow is a local-first realtime caption system. Its security model assumes a trusted operator machine and potentially untrusted audience clients, uploaded context, model servers, model output, and LAN peers.

## Trust boundaries

### Trusted operator boundary

The following capabilities are intentionally loopback-only:

- session start/stop;
- audio ingestion;
- glossary mutation/import/export;
- history mutation/deletion;
- model setup and sidecar lifecycle;
- preflight and diagnostics.

Reverse proxies must not expose those endpoints to the LAN or public Internet without adding a separate authentication layer.

### Audience boundary

Audience REST/WebSocket endpoints are read-only. A join code acts as a bearer capability for the active session. Invalid-code attempts are rate limited per observed socket client address. Forwarding headers are not trusted for this decision.

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

A release candidate must satisfy `docs/RELEASE_CHECKLIST.md`. Any unresolved critical/high dependency advisory, exposed secret, operator endpoint reachable from a non-loopback client, or reproducible crash/data-loss defect blocks release.
