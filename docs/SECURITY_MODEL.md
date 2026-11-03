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

Stopping microphone capture invalidates pending permission, device recovery and WebAudio results; late streams are stopped and stale callbacks cannot reopen a socket or clean up a newer capture. Browser regressions exercise the actual controller with platform API fakes, without requesting real microphone access.

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

Live Ollama correction sends source transcript text to the operator-configured
`LANGTEXTFLOW_OLLAMA_URL`. An Ollama provider name does not establish that its
endpoint is local. Verify its destination and data handling before enabling
correction; the operator controls and their model help describe this distinction.

Backend model HTTP clients connect directly to the configured provider and ignore
ambient `HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` settings. VibeVoice's outbound
WebSocket likewise disables automatic proxy discovery, so loopback configuration
cannot silently send speech or context through an environment-selected proxy.
This also covers model setup and health/preflight probes. HTTPX environment TLS
overrides (`SSL_CERT_FILE`/`SSL_CERT_DIR`) are not used by these HTTP clients;
normal certificate verification remains enabled. A deliberately configured
non-loopback provider still receives the data authorized for that provider.

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
configuration/health responses. Ollama model-download progress uses the same
identity/no-redirect/error policy with a **per-line** 665,536-byte UTF-8 bound.
LF/CRLF and an unterminated final line are supported; small progress messages are
delivered immediately. The total NDJSON stream length and model-download byte count
are not subject to this per-line cap. Cancellation closes the progress stream;
HTTP or in-stream provider errors mark the setup job failed and release its active key.
An EOF without the final `status: success` record is a failed download, not completion.

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

### Rust lock audits (2026-11-04)

CI uses official `cargo-audit =0.22.2` (MSRV 1.88, Apache-2.0/MIT) with the
repository's Rust 1.90 toolchain. It snapshots the current official RustSec database
and records its commit, then audits **both complete lock files** with
`--deny warnings --json`, without advisory ignores or target filters. The
`rust-dependency-security` artifact retains both raw reports, strict exit codes,
three cross-target desktop graphs, and `qualification.json`. Existing Windows/macOS
platform-smoke jobs additionally check and upload their actual native-host graph;
an Intel-only metadata job uses the officially supported
[`macos-15-intel` runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
to check Intel macOS without repeating model/backend/UI tests:
cross-target Cargo trees alone do not include every host-conditional build edge.
Database/network/graph/tool failures fail the job. This fills the former Python/npm-only audit gap;
successful unsigned packaging alone does not establish dependency security.

The auditor runs from a temporary directory containing an explicit configuration
with yank checking enabled and terminal quiet mode disabled, overriding project/home
audit configuration. Locked Cargo metadata is fetched before auditing. Raw JSON
stderr errors fail qualification; a companion nonquiet, colorless terminal audit
also exposes registry-open/update failures that cargo-audit 0.22.2 hides in JSON
mode. Both lock hashes must remain unchanged before and after **every subprocess**.
An earlier implementation's green artifact (run 41689319049) contained registry
lookup errors and therefore proves advisory matching only, not complete yank checking.
Its original artifact remains preserved; that result must not be reused as full
dependency qualification.

The original desktop lock had 11 vulnerability findings at RustSec database
`6c5fedf8ff03c485ed674d8f941e0180f3f72d70`:

| Dependency | Patched lock | Advisory IDs |
|---|---|---|
| rustls 0.23.44 | 0.23.46 | 2026-0346, 2026-0285, 2026-0347, 2026-0348, 2026-0349 |
| rustls-webpki 0.103.15 | 0.103.16 | 2026-0382 |
| webpki-root-certs 1.0.9 | 1.0.10 | 2026-0383 |
| zip 4.6.1 | 9.3.0 | 2026-0429, 2026-0367, 2026-0368, 2026-0369 |

Advisory IDs above use the `RUSTSEC-` prefix; the pinned official
[database snapshot](https://github.com/RustSec/advisory-db/tree/6c5fedf8ff03c485ed674d8f941e0180f3f72d70/crates)
contains affected ranges and upstream fixes. Desktop Tokio is also 1.53.3 for
[RUSTSEC-2026-0386](https://rustsec.org/advisories/RUSTSEC-2026-0386.html).
The actual application calls `UpdaterExt::check`, `Update::download`, then
`Update::install`; TLS/certificate verification is a runtime dependency. The
Windows updater's ZIP extraction is also runtime code, after signature verification,
not a build-only dependency. These are distinct from Python recording ZIP import.

The compatible official
[updater 2.14.1 release](https://github.com/tauri-apps/plugins-workspace/releases/tag/updater-v2.14.1)
upgrades ZIP to v9; its
[pinned manifest](https://github.com/tauri-apps/plugins-workspace/blob/128190689dbe59b59d344e01bf07b00862aa3791/plugins/updater/Cargo.toml)
uses Tauri 2.12, Rust 1.90, and Apache-2.0/MIT. Its packaged VCS metadata matches
that commit. We retain default updater features and use Cargo resolution, without
a forced incompatible ZIP patch. Our callers do not destructure its changed
Windows `Error::Extract` type. Package builds must still pass on Windows/macOS;
this does not approve signed-update installation or a new release.

The patched complete desktop lock has **zero vulnerabilities but still two
warnings**, so strict cargo-audit still exits 1:

- [glib 0.18.5 unsoundness](https://rustsec.org/advisories/RUSTSEC-2024-0429.html)
  is reachable through GTK 0.18 in the Linux desktop runtime graph.
- [proc-macro-error 1.0.4 maintenance warning](https://rustsec.org/advisories/RUSTSEC-2024-0370.html)
  is in the Linux GTK/glib proc-macro build graph.

The Windows MSVC, macOS ARM64 and macOS Intel cross-target Cargo graphs contain neither
package. The qualification script checks their exact package/version absence in
all three normal/build/dev graphs, and the native Windows, macOS ARM64 and Intel
metadata checks must also pass. Only each runner's actual architecture is a
native-host observation; the Linux job's trees remain cross-target observations.
Any vulnerability, new warning, changed warning
package/version, missing graph, or warning present in a supported graph fails.
This is an evidenced platform qualification, not a clean whole-lock audit.
Linux desktop remains unqualified: Tauri 2.12/WRY's GTK 0.18 constraints cannot
select patched glib >=0.20 through a compatible lock update. Do not ship a Linux
desktop bundle under the Windows/macOS result.

Reproduce with the pinned tool and a current official database checkout:

```sh
cargo install cargo-audit --version 0.22.2 --locked
git clone --depth 1 https://github.com/RustSec/advisory-db.git /tmp/rustsec-advisory-db
python scripts/audit_rust_dependencies.py --database /tmp/rustsec-advisory-db
```
