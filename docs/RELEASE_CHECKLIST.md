# Release Checklist

A release candidate is not considered production-ready until every required item below is green or explicitly accepted as a documented residual risk.

## Build / repository hygiene

- [ ] `main` CI is green on the exact release commit.
- [ ] Python dependency audit reports no known critical/high vulnerabilities.
- [ ] Frontend dependency audit reports no known critical/high vulnerabilities.
- [ ] Static security scan is green or all findings are reviewed and documented.
- [ ] Secret scan is green.
- [ ] Frontend dependency lockfile is committed and CI uses the lockfile.
- [ ] Apache-2.0 license and security policy are present.
- [ ] Dependency/license inventory is reviewed for incompatible licenses.

## Adversarial validation

- [ ] Oversized/malformed audio frames are rejected without crashing the session.
- [ ] NaN/Inf/implausible PCM is rejected before ASR/RMS processing.
- [ ] Slow or broken audience WebSocket clients cannot stall other clients.
- [ ] Audience connection capacity and invalid join-code throttling behave as expected.
- [ ] Malformed/oversized PDF and DOCX inputs are rejected safely.
- [ ] DOCX archive bombs and XML DTD/entity declarations are rejected.
- [ ] Prompt-injection text in reference/glossary/transcript data remains untrusted data.
- [ ] Oversized/malformed model responses degrade safely.
- [ ] XSS-sensitive rendering paths do not use raw HTML injection.

## Reliability / failure injection

- [ ] ASR provider failure is surfaced and fallback works where configured.
- [ ] Translation failure preserves original captions.
- [ ] Correction failure falls back to deterministic/original text.
- [ ] SQLite write/disk-full failure preserves live in-memory captions and is surfaced.
- [ ] Queue/backpressure metrics remain bounded during stress.
- [ ] 30-minute target-hardware soak passes.
- [ ] 60-minute target-hardware soak passes.
- [ ] 90-minute target-hardware soak passes without latency drift or memory growth outside the accepted envelope.
- [ ] Real Korean and English event samples pass failover/quality validation.

## Privacy / distribution

- [ ] API keys are backend-only and absent from browser/network payloads.
- [ ] Raw uploaded reference-file bytes are not persisted.
- [ ] Data/cache/database locations and deletion workflow are documented.
- [ ] Windows/macOS installers are signed for public distribution.
- [ ] Third-party notices/SBOM are generated for the release artifact.
- [ ] Privacy/security review has no unresolved release-blocking issue.

## Release blockers

The release is blocked by any of the following:

- known critical/high exploitable dependency vulnerability;
- secret or private credential committed to the repository/artifact;
- non-loopback access to operator/setup/audio endpoints without separate authentication;
- reproducible crash, transcript corruption, or data loss under supported usage;
- failure of original captions when an optional translation/correction provider fails;
- unreviewed license incompatibility;
- failing CI or release-gate benchmark on the release commit.
