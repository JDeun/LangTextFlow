# 입타자(Iptaja) 경쟁 제품 리서치 — LangTextFlow 적용 평가
> 2026-10-22 · 조사 범위: 공개 제품 설명 및 LangTextFlow README/BENCHMARK. 경쟁사 내부 구현 미확인. Tracking: #209

## 요약
입타자는 클라우드 기반 macOS/Windows 받아쓰기 앱이고 LangTextFlow는 local-first 다국어 실시간 자막·번역 시스템이다. 공통 기술 과제는 낮은 지연의 안정적 확정, 침묵/재발화 처리, 전문용어, code-switching. 경쟁 제품이 더 빠르거나 정확하다는 실측 근거는 없다.

## 공개 확인 사항과 근거
| 제품 관찰 | 근거 | 내부 구현에 대한 한계 |
|---|---|---|
| 발화 중 확정 문장 순차 출력 | [공식 홈페이지](https://iptaja.app/) | decoder, chunk 크기, commit 규칙 비공개 |
| 침묵 자동 대기/재발화, 화면 꺼짐 시 중단 | [사용 가이드](https://iptaja.app/blog/how-to-use-hotkey) | VAD 모델 비공개 |
| 선택 텍스트 단축키 사전 등록; 분야 프리셋 | [공식 홈페이지](https://iptaja.app/) | biasing과 post-processing 구분 불가 |
| 한국어·영어 혼합 발화 | [소개 글](https://iptaja.app/blog/introducing-iptaja) | 언어별 CER/WER 미공개 |
| 커서 직접 입력, 클립보드 불변 | [소개 글](https://iptaja.app/blog/introducing-iptaja) | LangTextFlow 핵심 제품 범위 밖 |
| 클라우드 음성 처리, 서버 비저장 주장 | [개인정보 설명](https://iptaja.app/blog/voice-privacy) | 독립 보안 검증 아님 |

## 현재 LangTextFlow와 Gap
README 기준: AudioWorklet → bounded WebSocket → VibeVoice Streaming(primary)/faster-whisper(fallback) → Stabilizer → qwen3.5:4b correction → translategemma:4b translation fan-out → caption surfaces/SQLite. VAD는 RMS/energy 보조. Hotword/Glossary/자료 context 존재. source language는 세션 시작 전 수동 선택; code-switching 미지원. 이 내용은 README 명시 상태이며 실제 구현 세부 확인은 후속 이슈에서 수행.

## 우선순위별 실험 계획
### P0: Incremental commit (#210)
partial/stable/corrected/translated/committed 각 시각 측정. first stable p50/p95, token revision, premature commit, 번역 재작업 횟수 측정. baseline 대비 정확도 악화 없이 안정적 지연 개선되는 경우만 채택.
### P0: 제품 benchmark 기준선 (#214)
기존 docs/BENCHMARK.md의 ASR micro 및 full CaptionRuntime benchmark 구분 유지. 실제 교회 설교/강의 ko/en/ja, mixed speech, 조용한/잡음 환경, 동일 16k mono PCM16 WAV + 검수 transcript. 동일 장비, warm/cold, 반복 3회 이상, RTF/CER/WER/latency/RSS 기록. 30/60/90분 soak는 별도.
### P1: VAD (#211)
RMS vs Silero/WebRTC 후보; silence, music, room noise, pause, resumed speech. missed speech/false activation/resume latency/CPU 비교. 음성 손실이 발생하면 reject.
### P1: 사전 등록 UX (#212)
수정된 오인식 단어를 Hotword(ASR) 또는 Glossary(correction/translation)에 적절히 등록. 승인/되돌리기/중복 방지. ASR adapter별 bias 지원 여부 검증.
### P1: code-switch (#213)
ko-en 혼합, 고유명사, 약어, 성경 인명/지명; 고정 source language 기준선 vs 후보. term recall/CER/WER/translation consistency 비교.

## 의사결정 원칙
- 마케팅의 '700타'는 ASR 지연·정확도 지표가 아니다.
- 입타자 내부 모델, VAD, 안정화 정책은 추측하지 않는다.
- cloud vs local 결과를 동일 성능이라고 취급하지 않는다.
- 기능 추가 전 중복 구현을 점검하고 수치 개선과 회귀 검증을 요구한다.
- 외부 앱을 블랙박스 테스트할 경우 이용약관·개인정보·사용 허가를 준수한다.

## 후속 작업
- [#209](https://github.com/JDeun/LangTextFlow/issues/209) 총괄
- [#210](https://github.com/JDeun/LangTextFlow/issues/210) Incremental commit
- [#211](https://github.com/JDeun/LangTextFlow/issues/211) VAD
- [#212](https://github.com/JDeun/LangTextFlow/issues/212) Glossary UX
- [#213](https://github.com/JDeun/LangTextFlow/issues/213) Code-switch
- [#214](https://github.com/JDeun/LangTextFlow/issues/214) Benchmark

이 문서는 저장소 내부 wiki-style 문서이며 GitHub Wiki 별도 저장소에는 아직 게시하지 않았다.
