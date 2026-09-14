# LangTextFlow 문서

처음 사용하는 분은 기술 문서보다 **사용자 가이드**부터 읽는 것을 권장합니다.

## 처음 사용하는 분

| 문서 | 언제 읽나요? |
|---|---|
| [`USER_GUIDE.md`](USER_GUIDE.md) | 처음 실행하고 실제 자막 세션을 시작할 때 |
| [`HARDWARE_REQUIREMENTS.md`](HARDWARE_REQUIREMENTS.md) | 내 PC에서 어떤 모델 구성이 가능한지, 최소/권장 사양을 확인할 때 |
| [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) | 마이크·자막·번역·QR 접속에 문제가 있을 때 |
| [`FAQ.md`](FAQ.md) | 지원 범위, 로컬 실행, 저장, 다국어 등 기본 질문이 있을 때 |
| [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) | 현재 미지원 기능과 production acceptance 경계를 확인할 때 |

## 운영과 설정

| 문서 | 내용 |
|---|---|
| [`HARDWARE_REQUIREMENTS.md`](HARDWARE_REQUIREMENTS.md) | 최소/권장/행사 운영 하드웨어 프로필, GPU·메모리·스토리지·네트워크 기준 |
| [`ONBOARDING.md`](ONBOARDING.md) | first-run setup wizard |
| [`PREFLIGHT.md`](PREFLIGHT.md) | hardware/provider/model 준비 상태 진단 |
| [`DISPLAY_SETTINGS.md`](DISPLAY_SETTINGS.md) | Audience/Projector/OBS 표시 설정 |
| [`MODEL_SETUP.md`](MODEL_SETUP.md) | local runtime/model 준비와 복구 |
| [`MODEL_GUIDE.md`](MODEL_GUIDE.md) | ASR/correction/translation 모델의 역할, 기본값, 대안, 교체 기준 |
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
| [`MODEL_GUIDE.md`](MODEL_GUIDE.md) | 모델별 책임, drop-in 대안과 adapter 필요 대안 구분 |

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
| [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) | 현재 지원하지 않는 기능과 검증 경계 |

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
- [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) — 현재 미지원 기능과 field validation 경계

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
8. 사용자에게 중요한 미지원 기능은 [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md)에 명시합니다.
9. 기본 모델 교체는 공개 benchmark 점수만으로 결정하지 않고 LangTextFlow의 역할별 benchmark/quality gate를 통과시킵니다.
10. 하드웨어 사양은 추정치를 공식 지원으로 승격하지 않고 실기기 benchmark 결과가 쌓일 때 [`HARDWARE_REQUIREMENTS.md`](HARDWARE_REQUIREMENTS.md)에 compatibility matrix로 반영합니다.