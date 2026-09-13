# LangTextFlow 문서

처음 사용하는 분은 기술 문서보다 **사용자 가이드**부터 읽는 것을 권장합니다.

## 처음 사용하는 분

| 문서 | 언제 읽나요? |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | 처음 실행하고 실제 자막 세션을 시작할 때 |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | 마이크·자막·번역·QR 접속에 문제가 있을 때 |
| [`FAQ.md`](FAQ.md) | 지원 범위, 로컬 실행, 저장, 다국어 등 기본 질문이 있을 때 |

## 운영과 설정

| 문서 | 내용 |
|---|---|
| [`ONBOARDING.md`](ONBOARDING.md) | first-run setup wizard |
| [`PREFLIGHT.md`](PREFLIGHT.md) | hardware/provider/model 준비 상태 진단 |
| [`DISPLAY_SETTINGS.md`](DISPLAY_SETTINGS.md) | Audience/Projector/OBS 표시 설정 |
| [`MODEL_SETUP.md`](MODEL_SETUP.md) | local runtime/model 준비와 복구 |
| [`GLOSSARY_TRANSFER.md`](GLOSSARY_TRANSFER.md) | glossary import/export |
| [`CONTEXT_DOCUMENTS.md`](CONTEXT_DOCUMENTS.md) | TXT/MD/PDF/DOCX reference context |

## 개발자와 기여자

### Architecture & realtime runtime

| 문서 | 내용 |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 전체 realtime pipeline과 주요 component |
| [`FAILOVER.md`](FAILOVER.md) | VibeVoice → faster-whisper failover |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | queue, latency, provider health, telemetry |
| [`LANGUAGE_FLOW.md`](LANGUAGE_FLOW.md) | source/target language와 multi-target fan-out |
| [`VIBEVOICE.md`](VIBEVOICE.md) | VibeVoice sidecar/runtime lifecycle |
| [`FASTER_WHISPER.md`](FASTER_WHISPER.md) | faster-whisper adapter/fallback |

### UI/UX

| 문서 | 내용 |
|---|---|
| [`DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md) | Operator/Audience/Projector/Onboarding의 visual, responsive, i18n, accessibility contract |

### 품질과 acceptance

| 문서 | 내용 |
|---|---|
| [`BENCHMARK.md`](BENCHMARK.md) | ASR/full-runtime benchmark와 soak harness |
| [`TRANSLATION_BENCHMARK.md`](TRANSLATION_BENCHMARK.md) | translation latency/quality protocol |
| [`CORRECTION_QUALITY.md`](CORRECTION_QUALITY.md) | correction quality gate |
| [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) | 자동 gate와 실제 장비/field acceptance |

### Security & adversarial validation

| 문서 | 내용 |
|---|---|
| [`SECURITY_MODEL.md`](SECURITY_MODEL.md) | trust boundary와 attack surface |
| [`ADVERSARIAL_VALIDATION.md`](ADVERSARIAL_VALIDATION.md) | malformed input/WebSocket/document/LLM/persistence adversarial contract |
| [`AUDIENCE_SECURITY.md`](AUDIENCE_SECURITY.md) | LAN audience isolation과 rate limiting |
| [`../SECURITY.md`](../SECURITY.md) | 공개 취약점 제보 정책 |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | 개발·검증·PR 기여 규칙 |

## 프로젝트 상태

- [`ROADMAP.md`](ROADMAP.md) — 구현 상태와 남은 acceptance
- [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) — release 판단 기준

> [!IMPORTANT]
> 자동 benchmark/soak harness가 존재하는 것과 실제 대상 장비·현장 음원에서 acceptance가 완료된 것은 다릅니다. 실제 측정 결과가 확보되기 전에는 production-ready로 간주하지 않습니다.

## 문서 유지 원칙

1. 구현되지 않은 기능을 완료된 것처럼 쓰지 않습니다.
2. 자동 harness와 실제 측정 결과를 구분합니다.
3. 외부 model/runtime 라이선스와 LangTextFlow의 Apache-2.0 라이선스를 구분합니다.
4. operator-only 기능과 LAN audience의 보안 경계를 문서와 코드에서 동일하게 유지합니다.
5. 기능 완료 여부는 README보다 `ROADMAP.md`와 CI gate를 우선합니다.
6. repository-local Markdown link는 CI에서 검증합니다.
7. UI 변경은 [`DESIGN_SYSTEM.md`](DESIGN_SYSTEM.md)의 제품 계약을 따릅니다.
