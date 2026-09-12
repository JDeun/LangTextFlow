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
- [x] QR code 렌더링 + LAN audience URL
- [x] SQLite 세션/transcript 영속화
- [x] Context 문서 업로드/추출 + ASR/번역 bounded reference context

## P1B — Real audio / ASR

- [x] 브라우저 마이크/오디오 인터페이스 장치 열거 및 선택
- [x] AudioWorklet 기반 mono float32 PCM 스트리밍
- [x] backend audio WebSocket + bounded provider queue
- [x] VibeVoice-ASR-Streaming sidecar adapter
- [x] Context/Glossary → VibeVoice context_info 전달
- [x] faster-whisper local micro-batch adapter
- [x] faster-whisper initial_prompt + hotwords + source language 전달
- [x] `Auto` startup fallback: VibeVoice → faster-whisper
- [x] fallback 이후 실제 active provider를 session state/history에 기록
- [x] RMS/energy monitoring VAD (audio frame은 제거하지 않음)
- [x] audio level / voice activity operator UI
- [x] ASR lag 계측
- [x] correction / translation / commit latency 계측
- [x] ASR / postprocess / persistence queue telemetry
- [x] audio enqueue backpressure counter + ASR queue high-watermark
- [x] provider runtime failure 상태 contract (`running` / `failure`)
- [x] VibeVoice sender/receiver 및 faster-whisper worker failure telemetry
- [x] 운영자 UI의 ASR `LIVE / STARTING / FAILED / IDLE` 상태 표시
- [x] `Auto` mid-session one-way failover: VibeVoice → faster-whisper
- [x] bounded PCM replay ring buffer + provider-local timestamp rebasing
- [x] replay 구간 timestamp/text overlap duplicate suppression
- [x] provider health monitor + feed-path failover trigger
- [x] failover count/reason telemetry + recovered operator state
- [x] ASR micro-benchmark harness (RTF / realtime lag / CER / WER / queue / RSS)
- [x] full runtime benchmark harness (ASR → correction → translation → persistence)
- [x] realtime/max pacing + 반복 fixture 기반 30/60/90분 soak 입력
- [ ] 실패 provider 재시도 / controlled failback 정책
- [ ] fuzzy duplicate suppression 필요성 benchmark
- [ ] semantic VAD/gating benchmark 및 필요 시 적용
- [ ] 한국어·영어 실제 집회 샘플 failover benchmark 실행
- [ ] 실제 대상 장비에서 30/60/90분 장시간 soak test 실행

## P2A — Correction / Translation

- [x] STABLE 이후 순차 post-processing pipeline
- [x] deterministic normalization / explicit alias correction
- [x] Church preset의 보수적 STT alias rules
- [x] translation provider abstraction
- [x] Ollama local translation provider
- [x] TranslateGemma 4B 기본 로컬 번역 모델 설정
- [x] 번역 장애 시 원문 자막 지속(degraded mode)
- [x] 임의 source language → 복수 target language fan-out
- [x] constrained LLM correction interface + Ollama adapter + safety gate/degraded fallback
- [x] segment별 correction provenance + SQLite/JSON export 보존
- [ ] correction confidence calibration / 품질 기준 정의
- [ ] 추가 local/cloud translation adapters

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
- [x] glossary import/export (JSON/CSV) + atomic upsert/skip 정책
- [ ] 최근 세션 기반 glossary 추천

## P3A — Audience onboarding / LAN

- [x] 브라우저가 현재 host 기준으로 audience API/WebSocket을 해석
- [x] private LAN / CGNAT(Tailscale 포함) IPv4 후보 탐색
- [x] 로컬 QR 생성 (외부 QR 서비스 불필요)
- [x] 복수 네트워크 주소 선택 UI
- [x] Vite dev/preview LAN bind
- [x] LAN origin CORS allowlist regex
- [x] operator REST/WebSocket/audio control을 loopback-only로 제한
- [x] audience REST/WebSocket만 join code 기반 LAN 접근 허용
- [x] join code rate limit / brute-force hardening
- [ ] mDNS 기반 사람이 읽기 쉬운 local hostname

## P3B — Session history / export

- [x] session metadata SQLite persistence
- [x] realtime path와 분리된 sequential persistence queue
- [x] segment latest-version upsert guard
- [x] 세션 종료 시점 기록
- [x] 최근 세션 history UI
- [x] 활성 세션 삭제 방지
- [x] SRT export
- [x] WebVTT export
- [x] TXT export
- [x] JSON export
- [x] target language export + source fallback
- [x] persistence failure degraded mode / UI warning
- [x] session detail/transcript 검색 UI
- [x] session 제목/메모 사후 편집 + original Session Context 보존

## P3C — Product UX

- [x] first-run onboarding wizard + 재실행 진입점
- [x] system preflight / hardware capability 진단
- [x] VibeVoice / faster-whisper / Ollama / translation model readiness 진단
- [x] NVIDIA GPU / Apple Silicon / RAM / disk / microphone readiness UI
- [x] 현재 선택 구성 기준 blocking preflight
- [x] 설치/실행 가능한 provider 기반 권장 구성 + one-click 적용
- [x] 세션 시작 직전 blocking preflight guard
- [x] Ollama translation model pull/status/cancel UI
- [x] faster-whisper package와 model cache readiness 분리
- [x] faster-whisper model prefetch/cache + cancellable setup job
- [x] onboarding / preflight에서 누락 model repair action
- [x] 준비된 VibeVoice sidecar start/status/stop lifecycle + external ownership 보호
- [x] 임의 source → 복수 target 운영자 UX + 원문 track 상시 제공
- [x] audience/projector/OBS에서 원문 또는 번역 언어 선택
- [x] 세션 Display Profile: 글꼴/크기/행수/유지시간/원문 병기/정렬 + 출력 화면 공통 적용
- [x] glossary JSON/CSV import/export UX + 중복 처리 정책 선택
- [ ] VibeVoice runtime/repository/model 자동 설치·검증
- [ ] faster-whisper runtime package 자동 설치/복구
- [ ] Ollama application 자체 설치/실행
- [ ] 실제 benchmark 기반 ASR model/device/compute 추천 preset
- [ ] glossary 추천 UX

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
