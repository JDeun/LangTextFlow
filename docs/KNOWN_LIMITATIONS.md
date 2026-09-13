# Known Limitations

이 문서는 LangTextFlow의 현재 지원 범위와 아직 production acceptance가 끝나지 않은 영역을 명확히 구분합니다.

## 현재 지원하지 않는 기능

### 시스템 오디오 직접 캡처

현재 입력은 브라우저 `getUserMedia` 기반의 **마이크 또는 오디오 인터페이스**입니다.

Zoom, Teams, Meet, 브라우저 영상, OS 전체 출력처럼 **컴퓨터가 재생하는 시스템 오디오를 앱이 직접 캡처하는 기능은 아직 제공하지 않습니다.** 필요한 경우 가상 오디오 장치 또는 물리적 오디오 라우팅을 별도로 구성할 수 있지만, 이는 현재 LangTextFlow가 자동으로 관리하는 제품 경로가 아닙니다.

### 자동 원본 언어 감지 / code-switching

현재 세션 시작 전에 발표자의 원본 언어를 사용자가 선택합니다.

- 자동 language detection
- 한 세션 안에서 여러 언어를 자유롭게 오가는 code-switching
- 언어가 바뀔 때 자동으로 source language를 전환하는 기능

은 아직 제품 기능으로 제공하지 않습니다.

### 완전 오프라인 보장

LangTextFlow는 local-first이지만 모든 상황에서 완전 오프라인인 것은 아닙니다.

네트워크가 필요할 수 있는 경우:

- 모델/runtime 최초 다운로드
- Ollama model pull
- VibeVoice runtime/model provisioning
- 사용자가 OpenAI-compatible 외부 provider를 선택한 경우
- release/update 확인을 사용하는 경우

이미 필요한 runtime/model이 로컬에 준비되어 있고 외부 provider를 선택하지 않았다면 핵심 자막·번역 경로를 로컬 위주로 구성할 수 있습니다.

## 현재 검증 경계

### 자동화로 검증된 영역

현재 CI는 다음 범위를 자동 검증합니다.

- backend unit/integration/property/adversarial regression
- Browser E2E
- Windows/macOS platform smoke
- Windows/macOS unsigned desktop bundle build
- Ruff / pytest / coverage
- Bandit / CodeQL
- pip-audit / npm audit
- Python/frontend SBOM
- repository / CSS / i18n hygiene
- axe 기반 WCAG 자동 접근성 검사
- synthetic lifecycle/load/soak regression

### 실제 환경에서 아직 검증이 필요한 영역

다음 항목은 자동 테스트만으로 완료 판정을 내리지 않습니다.

1. 실제 Windows code-signing certificate를 사용한 installer 서명
2. Apple Developer identity를 사용한 signing/notarization
3. 실제 installer install/update/uninstall lifecycle
4. 실제 signed updater endpoint를 통한 upgrade/recovery
5. 실제 마이크·오디오 인터페이스·GPU/CPU 조합
6. 실제 행사장 Wi-Fi/LAN 및 다수 청중 동시 접속
7. 실제 한국어·영어 현장 음원 품질 baseline
8. 실제 장비에서 30/60/90분 장시간 세션
9. 사람을 통한 keyboard/screen-reader 접근성 acceptance
10. 실제 사용 조직 기준 privacy/legal review

## 품질에 대한 주의

실시간 음성 인식과 번역 품질은 다음 요인의 영향을 받습니다.

- 마이크 품질과 마이크-화자 거리
- 방의 잔향과 배경 소음
- 화자의 발음, 속도, 억양
- 고유명사와 전문 용어
- 선택한 ASR/번역 모델
- CPU/GPU 성능
- target language 수
- correction/translation provider latency

따라서 특정 행사에서 사용하기 전에는 실제 발표 환경과 유사한 조건으로 사전 리허설하는 것을 권장합니다.

## 보안 경계

Desktop 패키지는 Audience/Projector/OBS surface를 LAN에 제공할 수 있지만 다음 경계를 유지하도록 설계합니다.

- Operator REST/WebSocket 제어는 loopback-only
- 오디오 입력/세션 제어는 LAN 청중에게 노출하지 않음
- Audience는 join code 기반 읽기 전용 경로
- 개발용 `/docs`, `/redoc`, `/openapi.json` 표면은 LAN에서 차단

Operator 화면이나 backend operator API를 임의의 reverse proxy 설정으로 외부 네트워크에 공개하는 것은 현재 지원되는 배포 방식이 아닙니다.

## 향후 후보 기능

아래 기능은 유용하지만 현재 release blocker로 간주하지 않습니다.

- OS-level system audio capture
- automatic language detection / code-switching
- 실제 benchmark에 기반한 hardware/model 자동 추천
- fuzzy duplicate suppression 고도화
- semantic VAD/gating
- speaker diarization/labeling 고도화
- PPTX/XLSX reference context 지원

기능 추가 여부는 실제 field acceptance 데이터와 사용 사례를 기준으로 결정합니다.
