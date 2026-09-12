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
- [ ] SQLite 세션/용어집 영속화

## P1B — Real audio / ASR

- [ ] 마이크/오디오 인터페이스 장치 열거 및 선택
- [ ] bounded audio queue + VAD
- [ ] VibeVoice-ASR-Streaming adapter
- [ ] faster-whisper fallback adapter
- [ ] Context/Glossary → VibeVoice hotword 전달
- [ ] draft/stable latency 계측
- [ ] 장시간 세션 backpressure / recovery
- [ ] 한국어·영어 실제 집회 샘플 벤치마크

## P2 — Correction / Translation

- [ ] deterministic normalization
- [ ] glossary manager UI (성경/교회 preset 포함)
- [ ] constrained LLM correction interface
- [ ] local/cloud translation adapters
- [ ] 용어집의 ASR hotword + correction + translation 동시 반영
- [ ] segment별 correction provenance / confidence

## P3 — Product UX

- [ ] onboarding wizard
- [ ] 모델 자동 다운로드/검증
- [ ] GPU/CPU 자동 감지와 권장 preset
- [ ] QR audience page + 다중 target language UX
- [ ] 글꼴/크기/행수/자막 유지시간 설정
- [ ] 세션 기록/검색/내보내기 (TXT/SRT/VTT/JSON)
- [ ] 최근 세션 기반 glossary 추천

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
