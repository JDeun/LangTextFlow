# faster-whisper fallback

LangTextFlow는 VibeVoice streaming sidecar와 별도로 `faster-whisper`를 로컬 ASR fallback으로 사용할 수 있습니다.

## 설치

`faster-whisper`는 모델 runtime과 CTranslate2 의존성이 크기 때문에 기본 backend dependency에는 포함하지 않습니다.

```bash
pip install -e '.[whisper]'
```

개발 의존성까지 함께 설치하려면:

```bash
pip install -e '.[dev,whisper]'
```

## 엔진 모드

운영자 UI의 ASR 선택지는 다음과 같습니다.

- `Auto`: VibeVoice를 먼저 시작하고 실패하면 faster-whisper를 시작합니다.
- `VibeVoice`: VibeVoice sidecar만 사용합니다.
- `faster-whisper`: faster-whisper만 사용합니다.
- `Demo`: 실제 오디오 없이 상태 파이프라인을 확인합니다.

`Auto` fallback은 **세션 시작 시점**에만 적용됩니다. 이미 시작된 VibeVoice가 세션 도중 종료된 경우 faster-whisper로 즉시 갈아타지는 않습니다. 중간 failover에는 최근 PCM replay buffer와 중복 segment 억제가 필요하기 때문에 별도 reliability 단계로 분리합니다.

## 기본 설정

```env
LANGTEXTFLOW_FASTER_WHISPER_MODEL=small
LANGTEXTFLOW_FASTER_WHISPER_DEVICE=auto
LANGTEXTFLOW_FASTER_WHISPER_COMPUTE_TYPE=default
LANGTEXTFLOW_FASTER_WHISPER_CHUNK_SECONDS=4.0
```

기본 모델을 `small`로 둔 이유는 fallback의 우선 목표가 최고 정확도가 아니라 **다양한 로컬 장비에서의 실행 가능성과 복구성**이기 때문입니다. 실제 현장 benchmark 후 장비별 권장 모델 preset을 별도로 제공할 예정입니다.

## Streaming 방식

faster-whisper 자체를 토큰 단위 streaming server로 취급하지 않습니다. LangTextFlow adapter가 브라우저의 16 kHz mono float32 PCM을 bounded queue로 받은 뒤, 기본 4초 micro-batch로 묶어 별도 worker thread에서 추론합니다.

```text
Browser PCM
  → /ws/audio
  → bounded queue
  → 4 s micro-batch
  → asyncio.to_thread(CTranslate2 inference)
  → STABLE TranscriptEvent
  → correction
  → translation
  → COMMITTED
```

세션 종료 시 4초보다 짧게 남은 residual PCM도 마지막 chunk로 flush합니다.

## Context / terminology

adapter는 현재 세션 context를 다음과 같이 전달합니다.

- title / presenter / description → `initial_prompt`
- hotwords + 활성 glossary term/alias → `hotwords`
- source language → `language`
- `beam_size=1`로 realtime latency 우선
- `condition_on_previous_text=False`로 독립 micro-batch의 누적 hallucination 완화
- `vad_filter=True`로 faster-whisper의 VAD filter 사용

LangTextFlow 자체의 RMS `EnergyVad`는 계속 monitoring용이며 audio를 제거하지 않습니다. faster-whisper 내부 VAD는 해당 provider의 decode 단계에서만 적용됩니다.

## Timing / observability

각 micro-batch의 시작 offset과 faster-whisper가 반환한 segment의 상대 start/end를 합산해 세션 전체 timestamp를 만듭니다. 따라서 기존 SRT/VTT export와 `audio end → STABLE` telemetry가 동일하게 동작합니다.

운영자 telemetry에서 특히 다음을 확인합니다.

- `ASR queue`: 지속 상승하면 faster-whisper의 realtime factor가 1보다 나쁠 가능성이 큽니다.
- `Audio enqueue`: queue가 가득 차면 browser audio 수신까지 backpressure가 전파됩니다.
- `ASR lag`: chunk 크기 + 실제 inference 시간이 함께 반영됩니다.

## 하드웨어

`device=auto`, `compute_type=default`를 기본값으로 사용해 CTranslate2가 가능한 실행 경로를 선택하도록 합니다. 실제 배포 단계에서는 GPU/CPU 자동 감지 결과에 따라 model/device/compute type을 추천하도록 확장할 예정입니다.

Windows NVIDIA 환경에서는 CUDA runtime 호환성도 함께 검증해야 하며, macOS에서는 CTranslate2/faster-whisper가 현재 LangTextFlow의 주력 경로가 아닙니다. Mac 배포용 ASR provider는 실제 benchmark 결과를 기준으로 별도 선택할 수 있습니다.

## 아직 하지 않는 것

- 세션 중 provider 자동 전환
- audio replay buffer 기반 seamless failover
- overlapping window / LocalAgreement 기반 Whisper partial stabilization
- 장비별 model auto-selection
- 모델 자동 다운로드 UI
- RTF 및 WER/CER benchmark 기반 preset
