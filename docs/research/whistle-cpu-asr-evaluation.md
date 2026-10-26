# Whistle CPU ASR — LangTextFlow 적합성 조사 (2026-10-26)

## 결정
영어 입력 전용 저사양 CPU 모드의 선택적 ASR 후보로 PoC 진행. 한국어·일본어 입력 미지원이므로 기본 모델 교체 금지.

## 사실 및 한계
- Cactus Compute Whistle: 16.9 MB 양자화 모델, Apache-2.0, CPU 실행.
- 16 kHz mono, 최대 30초 단위; en/de/fr/es/it/nl/pl 지원.
- Word-level timestamps, confidence, keyword biasing 제공.
- 공급사 발표 속도·WER는 LangTextFlow 장비/현장 환경에서 검증되지 않음.
- 기사 재현 테스트는 깨끗한 단일 화자 음원 위주. 장시간 스트리밍 성능의 근거로 사용하지 않음.

## 아키텍처 영향
기존 VibeVoice streaming primary와 faster-whisper fallback 유지. Whistle은 영어 입력에서만 명시적으로 선택하는 provider로 실험. 30초 chunk, overlap, VAD, 자막 partial/stable 변환과 지원 언어 가드가 필요. 한국어/일본어는 기존 ASR 경로 유지. 영어 입력→한국어/일본어 출력은 후단 번역에서 처리.

## 재현 및 평가
1. 모델·런타임 버전 고정, 다운로드 후 오프라인 재실행.
2. 영어 설교/강연 30·60·90분과 잡음·복수 화자·성경 고유명사 세트 구성.
3. 동일 CPU에서 faster-whisper tiny/base/small 비교. WER, RTF, p50/p95 latency, peak RAM, CPU, 누락률 기록.
4. keyword biasing 유무 비교 및 오탐 분석.
5. Windows/macOS/Linux 호환성 및 재배포 라이선스 검토.

## 근거 및 후속
- 기사: https://dev.to/jamilxt/whistle-a-169-mb-speech-to-text-model-that-runs-on-any-cpu-hands-on-test-3175
- 모델: https://huggingface.co/Cactus-Compute/whistle
- 추적 이슈: https://github.com/JDeun/LangTextFlow/issues/274
