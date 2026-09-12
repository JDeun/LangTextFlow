# System Preflight

LangTextFlow의 operator 화면은 세션 시작 전에 로컬 실행 환경을 진단합니다. 목적은 개발자가 아닌 사용자가 `왜 자막이 시작되지 않는지`를 터미널 로그 없이 이해할 수 있게 하는 것입니다.

## 진단 항목

backend `/api/v1/preflight`는 operator loopback 전용이며 다음 항목을 확인합니다.

- 운영체제 / architecture / Python version / CPU count
- Windows 포함 시스템 RAM
- LangTextFlow 데이터 경로 기준 여유 disk
- NVIDIA `nvidia-smi` 기반 GPU / VRAM / driver 정보
- Apple Silicon 환경 식별
- faster-whisper Python package 설치 여부
- VibeVoice `/v1/config` sidecar health
- Ollama `/api/tags` health
- 지정 번역 모델 존재 여부

브라우저 UI는 별도 버튼을 통해 다음을 확인합니다.

- microphone 권한
- audio input device 존재 여부

마이크 권한은 페이지 로드만으로 요청하지 않습니다. 사용자가 `마이크 점검`을 눌렀을 때만 `getUserMedia()`를 호출하고, 확인 직후 test stream track을 중지합니다.

## 선택 구성 기반 Ready 판정

Preflight 요청에는 현재 operator UI의 `engine`, `translation_provider`, `translation_model`을 전달합니다. 따라서 화면에서 설정을 바꾸면 점검 결과도 같은 구성으로 다시 계산됩니다.

ASR `Auto`는 다음 중 하나만 준비되어 있어도 시작 가능한 것으로 봅니다.

```text
VibeVoice READY
       OR
faster-whisper READY
```

즉 VibeVoice sidecar가 꺼져 있어도 faster-whisper가 준비되어 있으면 ASR 자체는 blocking failure가 아닙니다.

Ollama translation을 선택한 구성에서는 다음이 모두 필요합니다.

```text
Ollama API READY
       AND
translation model READY
```

`Demo engine`과 번역 비활성화 구성은 실제 provider 설치를 요구하지 않습니다.

세션 시작 버튼을 눌렀을 때도 동일한 preflight를 다시 실행합니다. blocking component가 있으면 실제 session runtime을 만들기 전에 사용자에게 누락 항목을 표시합니다.

RAM, disk, GPU 항목은 현재 advisory check입니다. 실제 hardware benchmark가 쌓이면 모델별 minimum/recommended requirement를 별도로 추가합니다.

## 권장 구성

Preflight는 benchmark가 없는 hardware 수치로 모델 성능을 추측하지 않습니다. 현재 설치되어 있고 실제 응답하는 provider를 기준으로 보수적인 권장 구성을 반환합니다.

- VibeVoice + faster-whisper 모두 준비: `Auto`
- VibeVoice만 준비: `VibeVoice`
- faster-whisper만 준비: `faster-whisper`
- 두 실제 ASR provider가 모두 없음: 자동으로 임의 provider를 추천하지 않음
- Ollama + 지정 번역 모델 준비: `Ollama`
- 번역 환경 미완성: 원문 자막 우선 `none`

operator의 preflight panel과 first-run onboarding wizard에서 `권장 구성 적용`을 누르면 실제 세션 설정 state에 즉시 반영됩니다.

## First-run onboarding

처음 operator 화면을 연 브라우저에서는 다음 순서의 onboarding wizard가 표시됩니다.

```text
Welcome
  → System preflight / recommendation
  → Microphone test
  → Source / target language + preset + providers
  → Ready
```

완료 상태는 브라우저 localStorage에만 기록합니다. 이후 상단 `초기 설정` 버튼으로 언제든 다시 열 수 있습니다.

## 상태 의미

- `ready`: 확인 완료
- `warning`: 실행 가능할 수 있으나 주의 필요
- `missing`: 선택 구성에 필요한 component가 없음
- `error`: component는 존재하지만 점검 실패
- `info`: 선택 사항 또는 정보성 항목

## 개인정보 / 보안

Preflight endpoint는 audience LAN endpoint가 아니라 operator-only endpoint입니다. 시스템 hardware와 local service 상태를 외부 audience client에 노출하지 않습니다.

현재 점검/온보딩 과정은 다음 작업을 자동으로 수행하지 않습니다.

- system package 설치
- driver 설치/업데이트
- 관리자 권한 요청
- 임의 service 종료

모델 다운로드와 sidecar lifecycle처럼 제품이 안전하게 소유할 수 있는 작업은 별도 setup manager에서 명시적인 사용자 동작, 진행 상태, 오류 복구를 포함해 구현합니다.

## 다음 단계

1. Ollama translation model pull/status/cancel UX
2. faster-whisper model cache/download manager
3. VibeVoice sidecar lifecycle manager
4. 실제 benchmark 기반 hardware/model recommendation
5. desktop packaging 이후 OS-level dependency installer
