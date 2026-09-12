# System Preflight

LangTextFlow의 operator 화면은 세션 시작 전에 로컬 실행 환경을 진단합니다. 목적은 개발자가 아닌 사용자가 `왜 자막이 시작되지 않는지`를 터미널 로그 없이 이해할 수 있게 하는 것입니다.

## 진단 항목

backend `/api/v1/preflight`는 operator loopback 전용이며 다음 항목을 확인합니다.

- 운영체제 / architecture / Python version / CPU count
- 시스템 RAM
- LangTextFlow 데이터 경로 기준 여유 disk
- `nvidia-smi` 기반 NVIDIA GPU / VRAM / driver 정보
- faster-whisper Python package 설치 여부
- VibeVoice `/v1/config` sidecar health
- Ollama `/api/tags` health
- 지정 번역 모델 존재 여부

브라우저 UI는 별도 버튼을 통해 다음을 확인합니다.

- microphone 권한
- audio input device 존재 여부

마이크 권한은 페이지 로드만으로 요청하지 않습니다. 사용자가 `마이크 점검`을 눌렀을 때만 `getUserMedia()`를 호출하고, 확인 직후 test stream track을 중지합니다.

## Ready 판정

기본 제품 구성은 `Auto ASR + Ollama translation`입니다.

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

RAM, disk, NVIDIA GPU 같은 항목은 현재 advisory check입니다. 부족하거나 NVIDIA GPU가 없어도 무조건 실행을 막지는 않습니다. 실제 hardware benchmark가 쌓이면 모델별 minimum/recommended requirement를 추가합니다.

## 상태 의미

- `ready`: 확인 완료
- `warning`: 실행 가능할 수 있으나 주의 필요
- `missing`: 선택 구성에 필요한 component가 없음
- `error`: component는 존재하지만 점검 실패
- `info`: 선택 사항 또는 정보성 항목

## 개인정보 / 보안

Preflight endpoint는 audience LAN endpoint가 아니라 operator-only endpoint입니다. 시스템 hardware와 local service 상태를 외부 audience client에 노출하지 않습니다.

현재 점검 과정은 다음 작업을 자동으로 수행하지 않습니다.

- package 설치
- model 다운로드
- driver 설치/업데이트
- service 실행/종료
- 관리자 권한 요청

자동 설치/수정은 별도 Product UX milestone에서 명시적인 사용자 동작과 진행 상태/취소/복구를 포함해 구현합니다.

## 다음 단계

1. 선택한 ASR/translation 설정과 preflight를 실시간 연동
2. hardware class 기반 권장 provider/model preset
3. VibeVoice sidecar lifecycle manager
4. faster-whisper model cache/download manager
5. Ollama model install/pull status
6. onboarding wizard에서 blocking check를 단계별 해결
