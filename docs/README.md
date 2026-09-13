# LangTextFlow Documentation

LangTextFlow의 설계·운영·provider·품질·보안 문서를 목적별로 정리한 인덱스입니다.

> [!NOTE]
> README는 제품의 현재 상태와 빠른 시작을 설명하고, 이 디렉터리는 구현 계약과 검증 기준을 더 자세히 기록합니다. 구현 상태의 최종 체크리스트는 [`ROADMAP.md`](ROADMAP.md), 릴리스 판단 기준은 [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)를 기준으로 합니다.

## Architecture & realtime runtime

| 문서 | 내용 |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 전체 realtime pipeline과 주요 component |
| [`FAILOVER.md`](FAILOVER.md) | VibeVoice → faster-whisper one-way failover, replay/rebase/dedup |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | queue, latency, provider health, failover telemetry |
| [`LANGUAGE_FLOW.md`](LANGUAGE_FLOW.md) | source/target language와 multi-target fan-out |
| [`DISPLAY_SETTINGS.md`](DISPLAY_SETTINGS.md) | Audience/Projector/OBS 공통 display profile |

## ASR, model setup & onboarding

| 문서 | 내용 |
|---|---|
| [`VIBEVOICE.md`](VIBEVOICE.md) | Microsoft VibeVoice streaming sidecar 구성과 lifecycle |
| [`FASTER_WHISPER.md`](FASTER_WHISPER.md) | faster-whisper adapter와 fallback 구성 |
| [`MODEL_SETUP.md`](MODEL_SETUP.md) | local model preparation job과 UI repair flow |
| [`PREFLIGHT.md`](PREFLIGHT.md) | hardware/provider/model readiness 진단 |
| [`ONBOARDING.md`](ONBOARDING.md) | first-run setup wizard와 권장 구성 적용 |

## Context, glossary & correction

| 문서 | 내용 |
|---|---|
| [`CONTEXT_DOCUMENTS.md`](CONTEXT_DOCUMENTS.md) | TXT/MD/PDF/DOCX context extraction과 bounded reference context |
| [`GLOSSARY_TRANSFER.md`](GLOSSARY_TRANSFER.md) | glossary JSON/CSV import/export와 conflict policy |
| [`CORRECTION_QUALITY.md`](CORRECTION_QUALITY.md) | correction benchmark, human review, release quality gate |

## Benchmark & acceptance

| 문서 | 내용 |
|---|---|
| [`BENCHMARK.md`](BENCHMARK.md) | ASR/full-runtime benchmark, pacing, soak harness |
| [`TRANSLATION_BENCHMARK.md`](TRANSLATION_BENCHMARK.md) | translation latency/quality/terminology benchmark protocol |
| [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) | automated gate와 실제 장비/field acceptance checklist |

> [!IMPORTANT]
> 30/60/90분 반복 입력이 가능한 soak **harness가 존재하는 것**과 실제 대상 장비에서 acceptance가 완료된 것은 다릅니다. 실제 한국어·영어 현장 음원과 배포 대상 장비의 장시간 검증 결과가 확보되기 전에는 production-ready로 과장하지 않습니다.

## Security & adversarial validation

| 문서 | 내용 |
|---|---|
| [`SECURITY_MODEL.md`](SECURITY_MODEL.md) | trust boundary, attack surface, realtime failure containment |
| [`ADVERSARIAL_VALIDATION.md`](ADVERSARIAL_VALIDATION.md) | malformed input, WebSocket, document, LLM, persistence adversarial contract |
| [`AUDIENCE_SECURITY.md`](AUDIENCE_SECURITY.md) | join code, LAN exposure, rate limiting, operator isolation |
| [`../SECURITY.md`](../SECURITY.md) | 공개 취약점 제보 정책 |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | 개발·검증·PR 기여 규칙 |

Repository CI는 Ruff, pytest/coverage, explicit adversarial regression gate, dependency audit, Bandit, repository hygiene, Markdown link integrity, Hugging Face local-cache policy, frontend audit/typecheck/build를 실행하며 CodeQL은 Python과 JavaScript/TypeScript를 분석합니다.

## Planning

| 문서 | 내용 |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | P0–P4 구현 상태와 상용 수준 완료 기준 |

현재 roadmap에서 큰 미완료 영역은 실제 field/soak baseline, app-managed runtime installation, desktop packaging/signing, diagnostics/update/offline cache, e2e/load/accessibility/i18n입니다.

## 문서 유지 원칙

문서와 구현이 어긋나지 않도록 다음 원칙을 사용합니다.

1. 구현되지 않은 기능은 완료된 것처럼 서술하지 않습니다.
2. benchmark harness와 실제 측정 결과를 구분합니다.
3. provider/model/runtime의 외부 라이선스와 LangTextFlow의 Apache-2.0 라이선스를 구분합니다.
4. 보안상 operator-only 기능과 LAN audience 기능의 경계를 문서와 코드에서 동일하게 유지합니다.
5. 기능 완료 여부는 README의 서술보다 `ROADMAP.md`와 자동 CI gate를 우선합니다.
6. 모든 repository-local Markdown link는 CI에서 실제 파일 존재 여부를 검사합니다.
