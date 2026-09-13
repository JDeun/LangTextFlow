# Model Guide

LangTextFlow는 하나의 거대한 모델에 모든 작업을 맡기지 않고, **음성 인식 → 자막 안정화/교정 → 번역**을 역할별로 분리합니다. 따라서 모델을 교체할 때는 단순 정확도뿐 아니라 streaming 특성, 지연, context 지원, 메모리 사용량, failure behavior를 함께 봐야 합니다.

> [!IMPORTANT]
> 아래에서 **현재 지원**은 LangTextFlow의 기존 adapter/config로 사용할 수 있다는 뜻입니다. **adapter 필요**는 모델 자체가 부적합하다는 뜻이 아니라, 현재 코드와 바로 호환되지 않는다는 뜻입니다. 새 모델을 기본값으로 바꾸기 전에는 `BENCHMARK.md`, `TRANSLATION_BENCHMARK.md`, `CORRECTION_QUALITY.md`의 gate를 통과해야 합니다.

## 한눈에 보기

| 단계 | 현재 기본값 | 역할 | 현재 경로에서의 대안 |
|---|---|---|---|
| Streaming ASR | `microsoft/VibeVoice-ASR-Streaming-7B` | 낮은 지연의 실시간 음성 인식, partial/stable 자막 생성 | `faster-whisper` 계열을 primary/fallback으로 사용 가능 |
| ASR fallback | `faster-whisper small` | VibeVoice startup/runtime failure 시 로컬 micro-batch ASR | faster-whisper가 인식하는 다른 Whisper/CTranslate2 모델 |
| Correction | `qwen3.5:4b` via Ollama | STT 오탈자·문맥 오류를 제한적으로 교정 | 다른 Ollama instruction model; OpenAI-compatible correction은 현재 별도 adapter 필요 |
| Translation | `translategemma:4b` via Ollama | source caption을 여러 target language로 번역 | 다른 Ollama text model 또는 OpenAI-compatible model |
| VAD | energy/RMS 기반, 모델 없음 | 음성 활동 상태와 telemetry 보조 | Silero VAD 등은 benchmark 후 adapter 추가 가능 |
| Speaker diarization | 현재 없음 | 화자 분리/라벨링 | pyannote 등은 별도 pipeline/adapter 필요 |

## 1. VibeVoice-ASR-Streaming-7B

### 역할

VibeVoice는 현재 LangTextFlow의 **primary streaming ASR**입니다.

파이프라인에서 담당하는 것은 다음과 같습니다.

```text
Microphone / audio interface
        ↓
AudioWorklet PCM stream
        ↓
VibeVoice-ASR-Streaming-7B
        ↓
PARTIAL / STABLE caption
```

LangTextFlow는 VibeVoice에 source language와 bounded context/glossary 정보를 전달하고, sidecar lifecycle과 health를 감시합니다. `Auto` 모드에서는 VibeVoice가 준비되어 있으면 먼저 사용하고, startup 또는 runtime failure 시 faster-whisper로 one-way failover합니다.

현재 repository와 model revision은 재현성을 위해 고정되어 있습니다.

### 장점

- streaming ASR을 전제로 한 primary 경로
- partial 결과를 빠르게 보여주는 realtime UX와 잘 맞음
- session context/glossary를 ASR 단계에 전달할 수 있음
- LangTextFlow의 health/failover contract가 이미 구현되어 있음

### 단점 / 주의점

- 7B급 모델이므로 hardware 요구량이 fallback보다 큼
- 실제 한국어·영어 현장 음원에서의 최종 quality/latency baseline은 field acceptance가 필요
- 모든 장비에서 VibeVoice를 강제하는 것이 최적이라는 의미는 아님

### 대안

**현재 코드에서 바로 가능한 대안:** `faster-whisper`를 선택해 VibeVoice 없이 세션을 운영할 수 있습니다.

**새 adapter가 필요한 후보:** 다른 native streaming ASR 서버/모델을 붙일 수 있지만, 최소한 다음 contract를 구현해야 합니다.

- PCM feed
- partial/stable event mapping
- source language/context 전달
- provider health/failure signal
- timestamp normalization
- cancellation/shutdown
- benchmark/soak gate

즉 모델 이름만 바꾸는 수준으로는 교체할 수 없습니다.

## 2. faster-whisper

### 역할

faster-whisper는 기본적으로 **fallback ASR**이며, 필요하면 직접 ASR engine으로 선택할 수도 있습니다.

현재 기본 model은 `small`입니다.

VibeVoice failure 시 LangTextFlow는 최근 PCM replay buffer를 이용해 faster-whisper로 one-way failover하고 timestamp/text overlap을 억제합니다.

### 왜 `small`이 기본인가

`small`은 기본값일 뿐 절대적인 최적값은 아닙니다. LangTextFlow는 아직 특정 hardware에 대해 `small`이 최적이라고 주장하지 않습니다. 실제 장비 benchmark가 완료되기 전에는 속도·메모리·정확도 사이의 보수적인 시작점으로 사용합니다.

### 같은 adapter에서 고려할 수 있는 대안

faster-whisper가 로드할 수 있는 Whisper/CTranslate2 계열 모델이라면 설정으로 교체할 수 있습니다. 대표적인 선택 방향은 다음과 같습니다.

| 방향 | 예시 | 기대 효과 | 비용 |
|---|---|---|---|
| 초저사양 / CPU 우선 | `tiny`, `base` | 빠른 로딩, 낮은 메모리 | 정확도 하락 가능 |
| 균형 | `small`, `medium` | 정확도/속도 균형 | 더 높은 연산량 |
| 정확도 우선 | `large-v3` 계열 | 어려운 음향·다국어 정확도 개선 가능 | latency/VRAM/RAM 증가 |
| 속도 최적화 계열 | `turbo` 등 compatible checkpoint | 큰 모델 대비 지연 절감 가능 | 실제 LangTextFlow workload benchmark 필요 |

> 모델 이름과 실제 지원 여부는 설치된 faster-whisper/CTranslate2 버전이 인식하는 model ID를 기준으로 합니다. README의 예시를 호환성 보증으로 해석하면 안 됩니다.

### 언제 primary로 쓰나

- VibeVoice를 구동하기 어려운 장비
- 설치 단순성이 더 중요한 환경
- GPU 없이 CPU/Apple Silicon 중심으로 먼저 검증할 때
- VibeVoice보다 faster-whisper가 실제 benchmark에서 더 안정적인 장비

## 3. Qwen 3.5 4B — Correction

현재 correction 기본값은 Ollama의 `qwen3.5:4b`입니다.

### 역할

Correction은 번역 전에 들어가며, **STT 결과를 새로 작성하는 단계가 아닙니다.** 목적은 제한적이고 보수적인 오류 교정입니다.

```text
STABLE ASR text
      ↓
Deterministic normalization / aliases
      ↓
Constrained LLM correction
      ↓
Safety gate
      ↓
CORRECTED text
```

숫자, 고유명사, critical token을 함부로 바꾸면 오히려 품질이 나빠지므로 LangTextFlow는 correction provenance와 safety gate를 별도로 둡니다.

### 왜 작은 instruction model을 쓰나

Correction은 긴 추론보다 **짧은 지연 + 지시 준수 + 최소 수정**이 중요합니다. 더 큰 모델이 항상 더 좋은 선택은 아닙니다. 큰 모델이 문장을 과도하게 재작성하면 harmful correction이 증가할 수 있습니다.

### 대안

현재 Ollama adapter에서는 다른 instruction-tuned text model을 설정할 수 있습니다. 예를 들어 같은 Qwen 계열의 다른 크기나 Gemma/Llama 계열처럼 Ollama에서 제공되는 일반 instruction model을 시험할 수 있습니다.

단, 기본값 변경 기준은 모델의 일반 benchmark가 아니라 LangTextFlow의 correction corpus에서 다음을 비교하는 것입니다.

- harmful change rate
- missed correction rate
- wrong change rate
- numeric/critical-token safety
- correction latency
- timeout/degraded-mode 빈도

`CORRECTION_QUALITY.md`의 기준을 통과하지 않으면 기본 모델로 올리지 않습니다.

## 4. TranslateGemma 4B — Translation

현재 로컬 번역 기본값은 Ollama의 `translategemma:4b`입니다.

### 역할

Correction이 끝난 source caption을 복수 target language로 fan-out합니다.

```text
CORRECTED source
     ├─→ English
     ├─→ 日本語
     ├─→ Español
     └─→ ...
```

번역 provider가 실패해도 source caption은 계속 유지되는 degraded-mode contract를 사용합니다.

### 대안 1 — 다른 Ollama model

현재 Ollama provider는 model 이름을 설정으로 교체할 수 있으므로 다른 text/translation-capable model을 실험할 수 있습니다.

다만 일반 대화 성능보다 다음이 중요합니다.

- 짧은 caption 단위 번역 latency
- terminology/glossary 준수
- 숫자·고유명사 보존
- target language 자연스러움
- 여러 target 동시 fan-out 시 throughput

### 대안 2 — OpenAI-compatible provider

LangTextFlow는 OpenAI-compatible `/v1` translation adapter도 제공합니다. 따라서 LM Studio, vLLM 또는 호환 endpoint에서 제공하는 모델을 translation model로 사용할 수 있습니다.

이 경로의 장점은 모델 선택 폭이 넓다는 점이고, 단점은 endpoint 구성과 provider별 latency/privacy 차이를 사용자가 책임져야 한다는 점입니다.

### 별도 adapter가 필요한 대안

NLLB-200, SeamlessM4T 같은 dedicated translation stack이나 상용 번역 API를 쓰려면 현재 provider contract에 맞는 별도 adapter가 필요합니다. 모델 품질만 보고 바로 설정 이름을 넣어 사용할 수 있는 구조는 아닙니다.

## 5. VAD는 현재 AI 모델이 아닙니다

LangTextFlow의 현재 VAD는 RMS/energy threshold 기반입니다. audio frame을 삭제하는 aggressive gate가 아니라 voice activity 상태와 telemetry를 보조하는 용도입니다.

Silero VAD 같은 neural VAD를 도입할 수는 있지만 다음을 먼저 비교해야 합니다.

- 발화 시작/끝 지연
- 짧은 한국어 음절 누락
- 음악/박수/배경소음에서 false positive
- CPU overhead
- ASR latency에 미치는 영향

실제 음원 근거 없이 semantic/neural VAD를 기본값으로 넣지 않는 것이 현재 정책입니다.

## 6. 현재 없는 모델 계층

### Speaker diarization

현재 LangTextFlow는 화자 분리 모델을 사용하지 않습니다. pyannote 같은 diarization model을 붙이려면 speaker timeline과 caption segment를 정렬하는 별도 pipeline이 필요합니다.

### Source-language auto detection / code-switching

현재 source language는 operator가 직접 선택합니다. ASR 모델이 내부적으로 언어 감지 능력을 갖고 있더라도 LangTextFlow 제품 contract는 자동 감지나 문장 중간 code-switching을 보장하지 않습니다.

### System-audio understanding

OS system audio capture 자체가 아직 제품 입력 경로가 아니므로, 회의 앱의 system audio를 모델에 직접 보내는 구성도 현재 지원하지 않습니다.

## 모델을 바꿀 때의 의사결정 순서

1. **역할을 먼저 확인합니다.** ASR, correction, translation은 서로 대체 관계가 아닙니다.
2. **현재 adapter로 가능한지 확인합니다.** model name 변경으로 끝나는지, 새 provider/adapter가 필요한지 구분합니다.
3. **실제 목표 hardware에서 측정합니다.** 모델 크기만으로 latency/throughput을 예측하지 않습니다.
4. **품질과 실패 모드를 같이 봅니다.** 평균 품질보다 timeout, OOM, queue growth, harmful correction이 더 중요한 release blocker일 수 있습니다.
5. **LangTextFlow benchmark gate를 통과시킵니다.** 일반 공개 benchmark 점수만으로 기본 모델을 변경하지 않습니다.

## 권장 선택 전략

| 상황 | 권장 시작점 |
|---|---|
| 처음 설치 | `Auto` + 현재 기본 모델 유지 |
| GPU 여유가 있고 streaming 우선 | VibeVoice primary + faster-whisper fallback |
| 낮은 사양 / 단순 구성 | faster-whisper primary, 작은 model부터 benchmark |
| 로컬 개인정보 보호 우선 | Ollama correction + Ollama translation |
| 이미 로컬 LLM server 운용 중 | OpenAI-compatible translation provider 검토 |
| 정확도 최적화 | 더 큰 모델을 바로 채택하지 말고 실제 corpus benchmark 후 결정 |

## 현재 기본값의 위치

기본값은 `apps/server/langtextflow/config.py`에 정의되어 있습니다.

```text
VibeVoice ASR        microsoft/VibeVoice-ASR-Streaming-7B
faster-whisper ASR  small
Correction           qwen3.5:4b
Translation          translategemma:4b
```

환경변수는 `LANGTEXTFLOW_` prefix를 사용합니다. 모델 변경 전에 `.env.example`, `MODEL_SETUP.md`, `PREFLIGHT.md`도 함께 확인하십시오.

## 관련 문서

- [`MODEL_SETUP.md`](MODEL_SETUP.md) — 모델 다운로드/캐시/복구
- [`VIBEVOICE.md`](VIBEVOICE.md) — VibeVoice lifecycle
- [`FASTER_WHISPER.md`](FASTER_WHISPER.md) — faster-whisper adapter
- [`FAILOVER.md`](FAILOVER.md) — ASR failover
- [`BENCHMARK.md`](BENCHMARK.md) — ASR/full-runtime benchmark
- [`TRANSLATION_BENCHMARK.md`](TRANSLATION_BENCHMARK.md) — 번역 benchmark
- [`CORRECTION_QUALITY.md`](CORRECTION_QUALITY.md) — correction safety/quality gate
- [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) — 현재 지원 경계
