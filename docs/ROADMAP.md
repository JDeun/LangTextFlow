# Product Roadmap

## P0 — Foundation

- [x] Monorepo 구조
- [x] FastAPI API + WebSocket
- [x] Versioned caption segment state machine
- [x] ASR abstraction
- [x] Mock streaming engine
- [x] Operator UI / audience preview
- [x] Backend test + frontend build CI

## P1A — Product domain / delivery

- [x] 세션 identity + 공개 join code
- [x] Session Context 모델
- [x] Hotwords + glossary 도메인 모델
- [x] General / Church / Conference / Lecture preset
- [x] Audience 전용 REST/WebSocket 경로
- [x] Audience web view
- [x] Projector view
- [x] OBS Browser Source용 transparent view
- [x] 운영자 UI에서 공유 링크 제공
- [ ] QR code 렌더링
- [ ] Context 문서 업로드/추출
- [ ] SQLite 세션/transcript 영속화

## P1B — Real audio / ASR

- [x] 브라우저 마이크/오디오 인터페이스 장치 열거 및 선택
- [x] AudioWorklet 기반 mono float32 PCM 스트리밍
- [x] backend audio WebSocket + bounded provider queue
- [x] VibeVoice-ASR-Streaming sidecar adapter
- [x] Context/Glossary → VibeVoice context_info 전달
- [ ] faster-whisper fallback adapter
- [ ] VAD
- [ ] draft/stable latency 계측
- [ ] 장시간 세션 backpressure / recovery telemetry
- [ ] 한국어·영어 실제 집회 샘플 벤치마크

## P2A — Correction / Translation

- [x] STABLE 이후 순차 post-processing pipeline
- [x] deterministic normalization / explicit alias correction
- [x] Church preset의 보수적 STT alias rules
- [x] translation provider abstraction
- [x] Ollama local translation provider
- [x] TranslateGemma 4B 기본 로컬 번역 모델 설정
- [x] 번역 장애 시 원문 자막 지속(degraded mode)
- [ ] constrained LLM correction interface
- [ ] 추가 local/cloud translation adapters
- [ ] segment별 correction provenance / confidence

## P2B — Glossary persistence

- [x] SQLite glossary repository
- [x] glossary CRUD API
- [x] 운영자 glossary manager UI
- [x] 교회 기본 용어 preset import
- [x] 용어별 alias / 번역 / category / boost / enabled 상태
- [x] General / Church / Conference / Lecture 적용 범위
- [x] 세션 시작 시 preset별 활성 glossary snapshot
- [x] 동일 snapshot을 ASR context + correction + translation에 공유
- [x] 기존 DB에 `presets_json`을 추가하는 경량 migration
- [ ] glossary import/export (JSON/CSV)
- [ ] 최근 세션 기반 glossary 추천

## P3 — Product UX

- [ ] onboarding wizard
- [ ] VibeVoice sidecar 자동 설치/실행/상태 진단
- [ ] 번역 모델 자동 설치/실행/상태 진단
- [ ] 모델 자동 다운로드/검증
- [ ] GPU/CPU 자동 감지와 권장 preset
- [ ] QR audience page + 다중 target language UX
- [ ] 글꼴/크기/행수/자막 유지시간 설정
- [ ] 세션 기록/검색/내보내기 (TXT/SRT/VTT/JSON)
- [ ] glossary import/export 및 추천 UX

## P4 — Distribution / Reliability

- [ ] Tauri desktop shell 또는 동등한 desktop packaging
- [ ] Windows/macOS signed installer
- [ ] crash recovery / diagnostics bundle
- [ ] offline-first model cache
- [ ] update channel
- [ ] e2e/load/soak tests
- [ ] 접근성/키보드 내비게이션/i18n
- [ ] privacy/security review

## 상용 수준 완료 기준

1. 일반 사용자가 터미널 없이 설치/실행할 수 있다.
2. 오디오 장치/모델/GPU 문제를 UI가 진단하고 해결책을 제시한다.
3. 90분 이상 세션에서 지연 누적·메모리 누수 없이 동작한다.
4. 네트워크/번역 실패가 원문 자막을 중단시키지 않는다.
5. 업데이트/모델 다운로드/복구가 사용자 데이터 손실 없이 가능하다.
6. 설치 파일 서명, 개인정보 처리, 라이선스 고지가 완료되어 있다.
