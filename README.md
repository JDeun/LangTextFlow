# LangTextFlow

> Real-time multilingual captions with streaming ASR, transcript stabilization, domain-aware correction, and translation.

LangTextFlow는 강연·집회·컨퍼런스 환경에서 음성을 실시간으로 전사하고, 안정화·교정·번역한 뒤 웹/프로젝터/OBS에 자막으로 전달하는 도구를 목표로 합니다.

## 제품 원칙

- **Low latency, then stabilize**: 초저지연 draft를 먼저 보여주고 안정화 결과로 같은 segment를 갱신합니다.
- **Engine-agnostic**: VibeVoice, faster-whisper, sherpa-onnx 등 ASR 엔진을 어댑터로 교체할 수 있습니다.
- **Domain-aware**: 성경·교회·기술 등 도메인 용어집과 제한적 교정 레이어를 지원합니다.
- **Local-first**: 가능한 처리는 로컬에서 수행하고, 클라우드 기능은 명시적으로 선택합니다.
- **Audience-first UX**: 운영자는 복잡한 파이프라인 대신 시작/중지, 언어, 입력 장치만 다룹니다.

## 현재 상태 — P0 Foundation

현재 브랜치는 제품 기반을 구축합니다.

- FastAPI + WebSocket 실시간 이벤트 서버
- `PARTIAL → STABLE → CORRECTED → TRANSLATED → COMMITTED` 상태 모델
- ASR 엔진 추상화
- 실제 패치 동작을 확인할 수 있는 Mock streaming engine
- React/TypeScript 운영자 UI + audience preview
- CI: Python lint/test + frontend build

> 아직 실제 마이크 캡처와 VibeVoice/faster-whisper 연결은 포함하지 않습니다. P1에서 추가합니다.

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

브라우저에서 `http://localhost:5173`을 열고 **데모 시작**을 누르면 draft가 stable/corrected/translated 상태로 교체되는 흐름을 확인할 수 있습니다.

## 문서

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/ROADMAP.md`](docs/ROADMAP.md)

## 라이선스

첫 공개 릴리스 전에 라이선스 정책을 확정할 예정입니다. 외부 GPL 프로젝트의 코드는 포함하지 않습니다.
