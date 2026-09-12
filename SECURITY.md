# Security Policy

## Supported versions

LangTextFlow is pre-1.0 software. Security fixes are applied to the latest `main` branch and to the latest published release once releases begin.

## Reporting a vulnerability

Please do **not** open a public issue for a suspected vulnerability that could expose user data, credentials, local network services, or enable code execution.

Use GitHub's private vulnerability reporting / Security Advisory flow for this repository when available. Include:

- affected commit or release;
- reproduction steps or proof of concept;
- expected and observed behavior;
- impact and attack preconditions;
- suggested mitigation, if known.

Please avoid accessing data that is not yours, disrupting real events, or testing against systems without permission.

## Security boundaries

- Operator and setup APIs are intended to be reachable only from loopback.
- Audience endpoints are read-only and protected by a session join code plus invalid-code rate limiting.
- Uploaded reference documents, transcripts, glossary content, and external model output are treated as untrusted data.
- API credentials are backend-only configuration and must never be sent to audience/operator browser payloads.
- LangTextFlow does not persist original uploaded reference-file bytes after extraction.

See `docs/SECURITY_MODEL.md` for the detailed threat model and release checks.
