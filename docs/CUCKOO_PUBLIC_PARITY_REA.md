# Cuckoo public feature parity / REA evidence plan (2026-10-13)

Status: **public-source research only**. This document records advertised capabilities,
NOT independently measured Cuckoo performance, internals, or an authorization to
reverse engineer a third-party service.

## Public-source baseline

| Cuckoo advertised surface | LangTextFlow observable acceptance |
| --- | --- |
| KO, EN, JA and 20+ language selection, automatic language recognition | KO/EN/JA 6 directed pairs; manually selected source must work before auto language ID; wrong LID must never silently route translation |
| Context documents and keywords | Upload consented PDF/slide content, relevant per-utterance glossary, explicit per-language source/target mapping, on/off latency and quality |
| Personal/team dictionary and auto-keyword updates | Editable, idempotent glossary, session/project snapshots, no unexpected edits, human review of suggested new terminology |
| Desktop overlay, in-person/microphone and online meeting audio | Operator audio, permission denial, transparent overlay, auditable desktop install, platform-specific permissions |
| Audience QR, shareable output and OBS | Independent language selection, sync, reconnect, revoked access, OBS projector visibility and no wrong-language fallback |
| Meeting transcripts / corrections / summaries | Source-grounded history, versioned summaries, reviewed corrections and exports, retention controls |
| Simultaneous multilingual interpretation | Same utterance to two target languages, full end-to-end delay and backlog on Linux CPU; original language never presented as a translation |

Official public references:
- https://www.cuckoo.so/ko
- https://www.cuckoo.so/ko/events
- https://www.cuckoo.so/ko/desktop
- https://www.cuckoo.so/changelog
- https://www.cuckoo.so/pricing
- https://www.cuckoo.so/terms
- https://github.com/morluto/rea
- https://rea.tools/guides/browser/

Important differences: Cuckoo marketing does NOT document private model names,
infrastructure, post-ASR chunking algorithms, or an independently verified
latency distribution. Never infer these facts from DOM elements, minified code,
or product claims. Compare measured outcomes, not supposed internals.

## REA scope / authorization

REA (morluto/rea, MIT) can observe browser user events, accessibility text,
requests and responses **when you are authorized to observe the target**.
Cuckoo Terms of Service restrict unapproved access, commercial competitive use,
content copying and excessive automated requests
(https://www.cuckoo.so/terms). Accordingly:

- **Do not** capture Cuckoo's non-public source, session tokens, authenticated
  requests, headers or other users' transcripts; do not enumerate internal
  endpoints or perform high-volume scraping; do not defeat access limits.
- Public marketing pages and published changelog can be summarized manually
  with attribution without taking proprietary code as implementation input.
- Before controlled live Cuckoo service trials or deeper instrumentation,
  obtain any required permission and verify the terms for that account.
  Only use consented/synthetic test audio and no personal/secret documents.
- REA's immediate, safe application is to instrument **LangTextFlow's own**
  Operator/Glossary/Audience browser flows. There is no need to instrument
  Cuckoo to discover LangTextFlow's shortcomings.

## REA on LangTextFlow: controlled local workflow

Prerequisites: Node.js >=22.19 / Node.js >=24.11 or Node.js 26+, npm, and a
Chrome-family browser. REA's browser command requires an already-running
browser with a local remote debugging port and a dedicated temporary profile.
Use the checked REA package version and inspect its release notes. On Linux,
after starting LangTextFlow's local web dev server (example URL shown here):

```bash
# Dedicated isolated browser; NEVER use a browser profile containing credentials
chromium --remote-debugging-port=9222 \
  --user-data-dir=/tmp/ltf-rea-profile \
  http://127.0.0.1:5173/

# Find the local LangTextFlow browser target:
npx -y rea-agents@6.4.0 list-browser-targets \
  http://127.0.0.1:9222 --json

# Substitute the confirmed LangTextFlow TARGET_ID from the command above.
# Start observation BEFORE manually editing or importing a test glossary.
npx -y rea-agents@6.4.0 inspect-web-page \
  http://127.0.0.1:9222 TARGET_ID \
  --observation-ms 10000 \
  --include-accessibility-text --include-json-body-shapes --json
```

Use dummy glossary data only. Export sanitized event metadata, not private
audio, token headers, personal files or full session transcripts. A network
capture may reveal session IDs, URLs and secrets even with local endpoints;
review and redact before uploading any sanitized artifact. REA may need a
different flag syntax on a future version; this command follows the public
browser guide and should be validated with `--help` at execution time.
The demonstration route and port are examples, **not proof** a real dev server
is currently running.

### Operator acceptance scenarios

1. Open the standalone Glossary page from Operator without dropping the
   current session; check back/forward, URL reload and keyboard access.
2. Add EN source `grace` => KO `은혜` and JA source `恵み` => KO `은혜`;
   edit the KO source `은혜` => EN `grace` / JA `恵み`; export and import
   without overwriting previous operator edits.
3. Open church session with EN input / KO output; verify selected glossary
   snapshot, source-only English ASR hints, translated KO output, missing
   translation waiting state; change a glossary entry after session start
   and confirm the running session snapshot is unaffected.
4. Repeat JA=>KO and KO=>EN+JA, inspect projected/QR outputs; compare terms
   with glossary enabled/disabled, with identical audio. Record per-action
   accessibility state, client delay and API request count.
5. Compare no-glossary vs glossary-assisted **actual model outputs** and
   CPU p95/p99, queuing, memory, translation fidelity and source-language
   isolation. A post-hoc metric alone does not satisfy glossary support.

## Evidence format

For each feature, store:

- `source_url`, `observed_at`, `evidence_type`
  (public_claim / local_observation / independent_measurement)
- `feature`, `scenario`, `expected`, `observed`,
  `limitations`, `consent_or_auth_status`
- `source_audio_sha256`, `glossary_snapshot_sha256`,
  `model_id_and_revision`, `cpu_threads`, `ram_peak_mb`,
  `p50/p95/p99_ms`, `translation_quality` where measurable

A Cuckoo latency number must never be invented from public claims;
publish `not_measured` until a lawfully conducted comparison exists.
Do not copy Cuckoo site imagery or implementation into LangTextFlow.
