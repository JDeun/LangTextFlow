# 오픈소스 받아쓰기 기술 비교 — LangTextFlow
조사일 2026-10-22 · 총괄 #209 · 경쟁 제품 입타자 조사 PR #215

> 조사 수준: 7개 저장소 README 및 LangTextFlow README, docs/ARCHITECTURE.md, docs/MODEL_GUIDE.md, docs/BENCHMARK.md 교차검토. **실제 함수 단위 코드 감사, 라이선스 전문, 모델 실측은 아직 완료되지 않았다.** 후속 이슈에서 반드시 수행한다.

## 제품 및 설계 비교
| Repo | README에서 확인한 요소 | LangTextFlow 현재 구현/문서 | 재사용 가설 | 추적 |
|---|---|---|---|---|
| [Handy](https://github.com/cjpais/Handy) | Silero VAD, Whisper/Parakeet, 로컬 데스크톱 | RMS telemetry, VibeVoice/faster-whisper, Tauri | VAD 시작/종료 정책, 경량 모델 lifecycle | #216, #211 |
| [OpenWhispr](https://github.com/OpenWhispr/openwhispr) | whisper.cpp/sherpa-onnx, local/cloud, translation | ASR router, Ollama correction/translation, bounded queue | provider isolation, model adapter, degraded mode | #217, #210 |
| [Wisper](https://github.com/taraksh01/wisper) | local-first dictation, 사용자 단어·언어 설정 | Hotword/Glossary/context 있음; source language 고정 | correction feedback loop, code-switch 실험 | #218, #212, #213 |
| [FluidVoice](https://github.com/altic-dev/FluidVoice) | 로컬 음성 받아쓰기 앱 | Tauri shell + Python sidecar | startup/cold load, memory 관리 비교 | #219 |
| [OpenWhisper](https://github.com/Knuckles92/OpenWhisper) | 다중 ASR/미리보기 관련 제품 설명 | partial→stable→committed 상태기계 | revision/commit timing 및 모델 비교 | #220, #210 |
| [Hush](https://github.com/khawkins98/Hush) | 로컬 음성 입력 앱 | AudioWorklet→WS→ASR | 오디오 세션 수명주기·침묵 처리 | #221 |
| [AuraScribe](https://github.com/JeswinJestin/AuraScribe) | sherpa-onnx 기반 Moonshine/Parakeet/Dolphin/IndicConformer | sherpa-onnx는 architecture상 후보, 기본 provider는 VibeVoice/faster-whisper | CPU-friendly multilingual model 후보 검증 | #222, #214 |

## 기존 LangTextFlow와 중복 — 구현 전 확인
- 이미 있음: AudioWorklet, bounded WebSocket, ASR primary/fallback, partial/stable/committed, deterministic correction, glossary/hotwords, multi-target translation, runtime benchmark, 30/60/90분 soak 프로토콜.
- 명시적으로 미지원/미검증: source-language auto detection/code-switching, neural VAD, field CER/WER·latency acceptance, OS system-audio capture.
- 따라서 VAD·glossary·benchmark를 '신규 구현'으로 중복 개발하지 말고 **기존 경로의 교체 가능성/계측/품질**을 먼저 확인한다.

## 벤치마크 가설
1. **VAD** (#211/#216): RMS telemetry baseline vs Silero/WebRTC 후보. 음악·박수·긴 휴지·짧은 발화에서 missed speech, false trigger, resume latency, CPU. 입력 오디오 손실은 release blocker.
2. **Incremental stability** (#210/#220): first PARTIAL/STABLE, STABLE→COMMITTED p50/p95, revision count, premature commit, correction/translation rework. commit을 앞당겨도 CER·고유명사 recall 악화 금지.
3. **Provider/ASR** (#214/#217/#219/#222): 동일 WAV PCM16 mono 16k, cold/warm, 3회 이상. ko/en/ja + code-switch; CER/WER, RTF, p95, sidecar RSS/VRAM, fallback gap.
4. **Glossary** (#212/#218): correction-only vs ASR bias vs 결합. 신학 용어/인명/성경 장절 term recall, harmful change, numeric preservation, latency.
5. **Mixed language** (#213/#218): 고정 언어 baseline vs 자동 감지/멀티링구얼 후보. ko-en 혼합, en-ko 혼합, 일본어 구절, 전문용어 및 번역 오염률.

## 실행 프로토콜
기존 [BENCHMARK](../BENCHMARK.md)의 ASR micro와 full runtime을 분리하여 실행. 동일 녹음·검수 transcript·hardware, 3회 반복, warm/cold 분리, 성능 결과 익명화. 합격 gate는 BENCHMARK의 임시 기준을 사용하되 실제 측정 전 개선 수치를 약속하지 않는다.

## 설계 재사용 및 법적 경계
README 수준의 기능 유사성은 코드 재사용 허가가 아니다. 모든 저장소의 LICENSE, 종속성·모델 가중치 라이선스를 파일 단위로 검토한다. 외부 GPL 구현은 원칙적으로 패턴만 독립 구현하고 복사하지 않는다. 클라우드 전용·OS 커서 삽입 기능은 LangTextFlow의 라이브 자막 핵심 경로와 분리한다.

## 소스 코드 감사 체크리스트
- [ ] 7개 저장소의 핵심 실제 코드 경로와 SHA 확보
- [ ] LangTextFlow 대응 코드 경로·호출 순서·thread/async 경계 확인
- [ ] 재사용 가능한 패턴에 정확한 출처 명시
- [ ] 구현 전 benchmark 및 regression tests 정의
- [ ] 라이선스 검토 결과 기록

## 이슈
총괄 [#209](https://github.com/JDeun/LangTextFlow/issues/209), 실험 [#210–#214](https://github.com/JDeun/LangTextFlow/issues/210), 저장소별 감사 [#216](https://github.com/JDeun/LangTextFlow/issues/216), [#217](https://github.com/JDeun/LangTextFlow/issues/217), [#218](https://github.com/JDeun/LangTextFlow/issues/218), [#219](https://github.com/JDeun/LangTextFlow/issues/219), [#220](https://github.com/JDeun/LangTextFlow/issues/220), [#221](https://github.com/JDeun/LangTextFlow/issues/221), [#222](https://github.com/JDeun/LangTextFlow/issues/222).

이 파일은 git-managed wiki-style 문서다. GitHub Wiki 별도 저장소는 수정하지 않았다.
