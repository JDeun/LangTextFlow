<h1 align="center">LangTextFlow</h1>

<p align="center">
  <strong>말하는 순간 자막이 되고, 안정화되고, 필요한 언어로 전달됩니다.</strong><br>
  Local-first realtime multilingual captions for talks, worship services, conferences, and classrooms.
</p>

> [!IMPORTANT]
> 현재는 source-first alpha입니다. 핵심 realtime pipeline, web UI, local providers, security/recovery boundaries와 benchmark harness는 구현되어 있지만 signed Windows/macOS installer와 실제 대상 장비의 장시간 field acceptance는 아직 완료 전입니다.

LangTextFlow는 낮은 지연의 임시 자막을 먼저 표시하고 같은 segment를 안정화, 교정, 번역, commit하는 실시간 다국어 자막 플랫폼입니다.

## 핵심 구성

- Browser AudioWorklet -> bounded audio WebSocket
- VibeVoice streaming primary ASR + faster-whisper fallback
- deterministic / constrained LLM correction
- Ollama / OpenAI-compatible translation fan-out
- hotword, glossary, TXT/MD/PDF/DOCX context
- Operator / Audience / Projector / OBS delivery
- SQLite session history and SRT/WebVTT/TXT/JSON export
- en / ko / ja interface localization

## UI/UX 방향

인터페이스는 일반 사용자가 빠르게 시작할 수 있는 clean communication SaaS shell과 실시간 운영에 필요한 professional operator layer를 결합합니다. Operator는 중요한 상태를 숨기지 않되 엔진/모델/diagnostics를 progressive disclosure로 정리하고, Audience/Projector/OBS는 자막 가독성을 우선합니다.

상세한 UI 구조, responsive/i18n/accessibility 원칙과 regression contract는 [UI/UX Design System](docs/DESIGN_SYSTEM.md)을 참조하십시오.

## 처리 구조

```text
Microphone -> AudioWorklet -> ASR -> Stabilizer -> Correction -> Translation
                                                     |
                           Operator / Audience / Projector / OBS / SQLite
```

Caption lifecycle:

```text
PARTIAL -> STABLE -> CORRECTED -> TRANSLATED -> COMMITTED
```

## 시작하기

### Backend

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
uvicorn langtextflow.main:app --app-dir apps/server --reload --host 0.0.0.0 --port 8000
```

Auto / faster-whisper를 사용하려면 `pip install -e '.[dev,whisper]'`를 사용합니다.

### Frontend

```bash
cd apps/web
npm ci
npm run dev
```

Operator는 loopback에서 사용하고 LAN에는 join-code 기반 audience read-only 경로만 노출하는 것이 보안 계약입니다.

## 검증

자동 gate에는 backend adversarial/boundary/property regression, Ruff, pytest/coverage, dependency audit, Bandit, repository/CSS/i18n hygiene, frontend typecheck/build, Browser E2E, Windows/macOS boundary smoke와 CodeQL Python + JavaScript/TypeScript가 포함됩니다.

Benchmark harness와 실제 field acceptance는 구분합니다. 실제 한국어/영어 현장 음원과 대상 장비에서 30/60/90분 soak, latency/memory baseline과 사용자 acceptance는 별도로 수행해야 합니다.

## 문서

전체 문서 인덱스는 [docs/README.md](docs/README.md)를 참조하십시오.

빠르게 볼 문서:

- [Architecture](docs/ARCHITECTURE.md)
- [UI/UX Design System](docs/DESIGN_SYSTEM.md)
- [Roadmap](docs/ROADMAP.md)
- [Onboarding](docs/ONBOARDING.md)
- [Preflight](docs/PREFLIGHT.md)
- [Failover](docs/FAILOVER.md)
- [Observability](docs/OBSERVABILITY.md)
- [Security Model](docs/SECURITY_MODEL.md)
- [Release Checklist](docs/RELEASE_CHECKLIST.md)

## 현재 남은 핵심 과제

1. 실제 한국어/영어 현장 음원의 ASR/failover/translation/correction baseline
2. 대상 장비 30/60/90분 soak 및 memory/latency acceptance
3. VibeVoice/faster-whisper/Ollama app-managed installation lifecycle
4. desktop shell과 signed Windows/macOS installer
5. update channel과 offline-first model cache
6. load/accessibility/i18n field acceptance

세부 진행 상태는 [Roadmap](docs/ROADMAP.md)에 유지합니다.

## 라이선스

LangTextFlow 자체 코드는 Apache License 2.0입니다. 외부 model/runtime/dataset에는 각각의 라이선스가 적용됩니다.
