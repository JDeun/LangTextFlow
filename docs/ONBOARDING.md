# First-run Onboarding

LangTextFlow operator UI는 첫 실행 시 일반 사용자가 실제 자막 세션을 시작할 수 있도록 초기 설정 wizard를 제공합니다.

## 단계

1. **시작** — 초기 설정에서 수행할 점검을 설명합니다.
2. **시스템** — 현재 ASR/번역 구성으로 preflight를 실행하고 설치되어 있는 provider 기반 권장 구성을 제시합니다.
3. **마이크** — 사용자 동작으로 microphone 권한과 audio input 존재 여부를 확인합니다.
4. **언어** — source/target language, preset, ASR provider, translation provider를 설정합니다.
5. **완료** — 적용된 구성을 요약하고 operator workspace로 이동합니다.

## 상태 저장

완료 여부는 browser localStorage의 `langtextflow:onboarding:v1` 키에만 저장합니다. 서버 DB에는 browser onboarding 상태를 저장하지 않습니다.

상단 `초기 설정` 버튼으로 언제든 wizard를 다시 열 수 있습니다.

## 권장 구성 적용

wizard와 operator preflight panel은 동일한 `RecommendedConfiguration`을 사용합니다. 적용 시 별도 shadow state를 만들지 않고 OperatorApp의 실제 session 설정을 변경합니다.

현재 권장 로직은 hardware 수치만으로 모델 성능을 추정하지 않습니다. 실제로 설치되어 있고 health check를 통과한 provider만 사용합니다. benchmark 결과가 축적되면 hardware class 기반 model/device/compute preset을 추가합니다.

## 세션 시작 보호

wizard를 건너뛸 수는 있지만 실제 `세션 시작` 시 현재 선택 구성으로 preflight를 다시 실행합니다. blocking check가 남아 있으면 runtime 생성 전에 시작을 중단하고 누락된 component 이름을 operator에게 표시합니다.
