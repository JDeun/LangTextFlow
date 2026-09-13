# Release Checklist

A release candidate is not considered production-ready until every required item below is green or explicitly accepted as a documented residual risk.

> [!NOTE]
> 이 문서는 **특정 release commit의 acceptance**를 위한 체크리스트입니다. 저장소에 자동 검증 코드가 구현되어 있다는 사실과 실제 release artifact/현장 장비에서 acceptance가 완료됐다는 사실을 구분합니다.

## Automated contract already implemented

다음 검증은 repository CI/release workflow에 코드로 구현되어 있습니다. release 시에는 해당 exact commit에서 다시 green인지 확인해야 합니다.

- repository/secret-like hygiene와 Markdown link integrity
- Ruff, pytest/coverage, startup/lifespan smoke
- explicit adversarial/property/boundary/fan-out regression gate
- `pip check`, `pip-audit`, `npm audit`, Bandit
- Python/frontend CycloneDX SBOM 생성
- CSS/i18n hygiene와 en/ko/ja locale regression
- Browser Operator/Audience E2E 및 mobile overflow assertions
- axe WCAG A/AA/2.1/2.2 automated accessibility scan
- Windows/macOS backend/frontend boundary smoke
- synthetic lifecycle/load/soak regression harness
- CodeQL Python + JavaScript/TypeScript
- Windows/macOS Tauri bundle CI
- signed installer/updater release workflow와 external signing-secret hook

## Build / repository hygiene

- [ ] `main` CI is green on the exact release commit.
- [ ] Desktop packaging workflow is green on the exact release commit/PR head used for release.
- [ ] CodeQL is green on the exact release commit.
- [ ] Python dependency audit reports no known critical/high vulnerabilities.
- [ ] Frontend dependency audit reports no known critical/high vulnerabilities.
- [ ] Static security scan is green or all findings are reviewed and documented.
- [ ] Secret-like repository hygiene scan is green and no credential is present in the artifact.
- [ ] Frontend/Python lockfiles used for the release are committed and respected by CI.
- [ ] Apache-2.0 license and security policy are present.
- [ ] Dependency/license inventory/SBOM is reviewed for incompatible licenses.

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
- [ ] Operator-only controls remain unavailable through audience/LAN trust boundaries.

## Reliability / failure injection

### Automated before field testing

- [ ] ASR provider failure is surfaced and fallback works in automated regression fixtures.
- [ ] Translation failure preserves original captions.
- [ ] Correction failure falls back to deterministic/original text.
- [ ] SQLite write/disk-full failure preserves live in-memory captions and is surfaced.
- [ ] Queue/backpressure metrics remain bounded during synthetic stress.
- [ ] Runtime provisioning cancellation/shutdown paths pass.
- [ ] Cached-model inventory/clear operations stay within the managed cache root.
- [ ] Browser E2E, axe accessibility and en/ko/ja UI regression are green.

### Requires real hardware / field inputs

- [ ] 30-minute target-hardware soak passes.
- [ ] 60-minute target-hardware soak passes.
- [ ] 90-minute target-hardware soak passes without latency drift or memory growth outside the accepted envelope.
- [ ] Real Korean and English event samples pass failover/quality validation.
- [ ] Microphone/audio-interface hotplug and permission flows pass on target Windows/macOS machines.
- [ ] Real GPU/CPU/model combinations meet the accepted latency envelope.
- [ ] Human keyboard/screen-reader/accessibility acceptance passes.

## Privacy / distribution

### Code/repository checks

- [ ] API keys are backend-only and absent from browser/network payloads.
- [ ] Raw uploaded reference-file bytes are not persisted.
- [ ] Data/cache/database locations and deletion workflow are documented.
- [ ] Third-party notices/SBOM are generated for the release artifact.
- [ ] Updater artifacts require signature verification and updater configuration points only to the intended endpoint.

### External credentials / release operations

- [ ] Windows installer is signed with the production code-signing certificate.
- [ ] macOS artifact is signed with the production Developer ID and notarized.
- [ ] Signed updater artifact is published to the production update endpoint and an upgrade is exercised end-to-end.
- [ ] Installer install/update/uninstall/reinstall is accepted on target Windows/macOS machines.
- [ ] Privacy/legal review has no unresolved release-blocking issue.

## Release blockers

The release is blocked by any of the following:

- known critical/high exploitable dependency vulnerability;
- secret or private credential committed to the repository/artifact;
- non-loopback access to operator/setup/audio endpoints without separate authentication;
- reproducible crash, transcript corruption, or data loss under supported usage;
- failure of original captions when an optional translation/correction provider fails;
- unreviewed license incompatibility;
- failing CI, CodeQL, desktop packaging, accessibility gate, or release-gate benchmark on the release commit;
- missing production signing/notarization where a public installer is being distributed.
