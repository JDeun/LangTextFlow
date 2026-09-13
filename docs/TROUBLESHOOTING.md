# 문제 해결

LangTextFlow가 예상대로 동작하지 않을 때 개발 도구를 열기 전에 확인할 항목을 정리합니다.

## 1. 앱은 열리지만 시작할 수 없습니다

**Preflight / 준비 상태**를 먼저 확인하십시오. LangTextFlow는 선택한 음성 인식·번역 구성에 필요한 항목을 점검합니다.

- 마이크 권한
- ASR runtime/model
- 번역 provider
- 로컬 모델
- 시스템 준비 상태

표시된 복구 버튼이 있다면 먼저 해당 작업을 실행한 뒤 다시 점검합니다.

## 2. 마이크가 동작하지 않습니다

1. Windows/macOS 설정에서 LangTextFlow 또는 실행 중인 앱에 마이크 권한이 있는지 확인합니다.
2. LangTextFlow에서 올바른 입력 장치를 선택했는지 확인합니다.
3. Bluetooth/USB 장치를 방금 연결했다면 장치 목록을 다시 확인합니다.
4. 다른 프로그램이 장치를 독점하고 있지 않은지 확인합니다.

## 3. 음성은 들어오지만 자막이 없습니다

- ASR engine이 `ready`인지 확인합니다.
- Auto를 사용한다면 primary/fallback 상태를 확인합니다.
- 모델 설치 또는 cache 준비가 끝났는지 확인합니다.
- 시스템 부하가 지나치게 높지 않은지 telemetry를 확인합니다.

## 4. 원문은 나오지만 번역이 없습니다

- target language가 하나 이상 선택되어 있는지 확인합니다.
- Ollama/OpenAI-compatible provider 상태를 확인합니다.
- Ollama를 사용하는 경우 필요한 모델이 준비되어 있는지 확인합니다.
- 외부 provider라면 endpoint/network/API credential 설정을 확인합니다.

번역 provider가 실패하더라도 원문 자막은 가능한 한 계속 제공하도록 설계되어 있습니다.

## 5. Ollama가 설치되어 있는데 연결되지 않습니다

설치 여부와 실행 준비 상태는 다릅니다. LangTextFlow는 가능한 경우 Ollama runtime을 시작하고 health check를 수행합니다. 준비 상태에서 다시 시도하십시오.

LangTextFlow가 직접 시작한 프로세스만 앱 lifecycle에 따라 종료 대상으로 관리합니다.

## 6. 청중이 QR로 접속하지 못합니다

- Operator PC와 청중 기기가 같은 LAN/Wi-Fi에 있는지 확인합니다.
- 현재 세션의 QR/join 정보를 사용하고 있는지 확인합니다.
- VPN, guest Wi-Fi isolation, 방화벽 정책이 LAN 기기 간 통신을 막고 있지 않은지 확인합니다.

Operator 화면 자체를 LAN에 공개하는 방식으로 해결하지 마십시오. Audience는 별도의 읽기 전용 경계를 사용합니다.

## 7. 자막이 느려집니다

다음 지표를 확인합니다.

- ASR queue
- realtime lag
- provider latency
- translation latency
- CPU/GPU/RAM 사용량

큰 모델을 낮은 성능 장비에서 실행하면 realtime보다 처리 속도가 느려질 수 있습니다. 실제 배포 장비에서는 benchmark/soak 결과를 기준으로 모델을 결정해야 합니다.

## 8. 전문 용어가 자주 틀립니다

- Hotword에 중요한 고유명사를 추가합니다.
- Glossary에 전문 용어와 원하는 번역을 추가합니다.
- 발표 자료가 있다면 reference document로 추가합니다.
- 반복되는 미등록 용어는 glossary recommendation 후보를 검토합니다.

## 9. 기록 또는 export가 이상합니다

세션을 정상적으로 종료한 뒤 History에서 해당 세션을 다시 확인합니다. DB 쓰기 오류가 발생하더라도 live caption 경로 자체를 최대한 유지하도록 분리되어 있습니다.

중요한 행사에서는 종료 후 export 파일을 확인해 두는 것이 좋습니다.

## 10. 그래도 해결되지 않습니다

버그를 보고할 때 다음 정보를 함께 제공하면 원인 파악이 빨라집니다.

- 운영체제와 버전
- LangTextFlow 버전/commit
- 사용한 ASR engine/model
- 사용한 translation provider/model
- 재현 단계
- 화면에 표시된 오류
- 개인정보를 제거한 관련 로그

보안 취약점으로 판단되는 문제는 공개 issue 대신 저장소의 [Security Policy](../SECURITY.md)를 따르십시오.
