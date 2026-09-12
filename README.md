# LangTextFlow

> Real-time multilingual captions with streaming ASR, transcript stabilization, domain-aware correction, and translation.

LangTextFlow는 강연·집회·컨퍼런스 환경에서 음성을 실시간으로 전사하고, 안정화·교정·번역한 뒤 웹/프로젝터/OBS에 자막으로 전달하는 **local-first, self-hostable 실시간 자막 플랫폼**을 목표로 합니다.

첫 번째 vertical preset은 교회/선교 집회지만, 핵심 엔진은 일반 강연·컨퍼런스·교육 환경에서도 사용할 수 있도록 설계합니다.

## 제품 원칙

- **Low latency, then stabilize**: 초저지연 draft를 먼저 보여주고 안정화 결과로 같은 segment를 갱신합니다.
- **Engine-agnostic**: VibeVoice, faster-whisper 등 ASR 엔진을 provider로 교체할 수 있습니다.
- **Domain-aware**: 성경·교회·기술 등 도메인 context/hotword/glossary를 ASR·교정·번역에서 공유합니다.
- **Local-first**: 가능한 처리는 로컬에서 수행하고, 클라우드 기능은 명시적으로 선택합니다.
- **Audience-first UX**: 운영자는 복잡한 파이프라인 대신 세션, 언어, 입력 장치와 출력 화면만 다룹니다.

## 현재 구현 상태

### Realtime core

- FastAPI + WebSocket 실시간 이벤트 서버
- `PARTIAL → STABLE → CORRECTED → TRANSLATED → COMMITTED` 상태 모델
- `segment_id + version` 기반 자막 patch
- ASR provider abstraction
- Mock streaming engine

### Product / delivery

- Session Context + hotwords/glossary 도메인
- General / Church / Conference / Lecture preset
- 공개 audience join code
- Audience web view
- Projector full-screen view
- OBS Browser Source용 transparent view

### Real audio / VibeVoice

- 브라우저 마이크/오디오 인터페이스 선택
- AudioWorklet 기반 mono float32 PCM capture
- backend `/ws/audio` streaming endpoint
- bounded provider queue / backpressure
- Microsoft VibeVoice streaming sidecar adapter
- session context/hotwords → VibeVoice `context_info`

> VibeVoice 실제 모델 추론은 별도 sidecar가 필요합니다. 현재 CI는 provider protocol contract까지 검증하며 실제 GPU 모델의 품질/RTF/장시간 안정성은 별도 benchmark 단계입니다.

## 개발 실행

### Backend

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
uvicorn langtextflow.main:app --app-dir apps/server --reload
```

### Frontend

```bash
cd apps/web
npm install
npm run dev
```

브라우저에서 `http://localhost:5173`을 엽니다.

- **Demo engine**: 실제 모델 없이 draft → stable → corrected → translated → committed 흐름을 확인합니다.
- **VibeVoice Streaming**: 로컬 VibeVoice sidecar를 실행한 뒤 마이크/오디오 인터페이스를 선택해 실제 음성을 전송합니다.

## VibeVoice

기본 sidecar 주소는 `http://127.0.0.1:8001`입니다.

```env
LANGTEXTFLOW_VIBEVOICE_URL=http://127.0.0.1:8001
```

설치 및 실행 방법은 [`docs/VIBEVOICE.md`](docs/VIBEVOICE.md)를 참고하세요.

## 문서

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/ROADMAP.md`](docs/ROADMAP.md)
- [`docs/VIBEVOICE.md`](docs/VIBEVOICE.md)

## 아직 필요한 주요 작업

- faster-whisper fallback
- VAD / latency telemetry / backpressure observability
- 한국어·영어 현장 benchmark + 30/60/90분 soak test
- deterministic STT normalization
- glossary correction + translation providers
- QR audience onboarding
- 세션/용어집 SQLite persistence
- Tauri desktop shell, 모델/sidecar 자동 설치, hardware auto-detection
- signed Windows/macOS installer

## 라이선스

첫 공개 릴리스 전에 라이선스 정책을 확정할 예정입니다. 외부 GPL 프로젝트의 코드는 포함하지 않으며, CaptionFlow 등은 아키텍처 reference로만 사용합니다.
