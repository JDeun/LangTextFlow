# VibeVoice Streaming Provider

LangTextFlow는 Microsoft VibeVoice를 애플리케이션 프로세스에 직접 포함하지 않고 **로컬 ASR sidecar**로 연결합니다. 이 구조는 UI/세션/자막 파이프라인을 특정 모델 런타임과 분리하고, 향후 faster-whisper 등 다른 provider를 함께 지원하기 위한 선택입니다.

## 현재 연결 구조

```text
Browser / Desktop UI
  -> AudioWorklet (mono float32 PCM)
  -> LangTextFlow /ws/audio
  -> bounded audio queue
  -> VibeVoice WS /v1/stream
  -> stable transcript segments
  -> LangTextFlow caption state / audience delivery
```

LangTextFlow는 VibeVoice의 `/v1/config`에서 모델이 요구하는 sample rate와 chunk 정보를 읽고, `/v1/stream` WebSocket에 세션 context와 PCM frame을 전달합니다.

## VibeVoice runtime 준비

최초 runtime/model 준비 단계에서는 Microsoft VibeVoice 저장소의 공식 streaming launcher를 사용할 수 있습니다. LangTextFlow backend가 기본적으로 `8000` 포트를 사용하므로 VibeVoice는 `8001` 포트를 권장합니다.

```bash
git clone https://github.com/microsoft/VibeVoice.git
cd VibeVoice
python3 vllm_plugin/scripts/start_streaming_server.py \
  --model microsoft/VibeVoice-ASR-Streaming-7B \
  --port 8001
```

공식 launcher의 `--skip-deps`는 시스템 의존성 설치 단계를 건너뛰기 위한 옵션입니다. launcher 전체가 단순한 server start 명령은 아니며, VibeVoice 설치/모델 resolve/streaming checkpoint 준비 작업도 수행할 수 있습니다. 따라서 LangTextFlow backend는 이 launcher를 자동으로 실행하지 않습니다.

정상 실행 후 다음 endpoint가 응답해야 합니다.

```text
http://127.0.0.1:8001/health
http://127.0.0.1:8001/v1/config
ws://127.0.0.1:8001/v1/stream
```

다른 주소를 사용할 경우 LangTextFlow `.env`에 지정합니다.

```env
LANGTEXTFLOW_VIBEVOICE_URL=http://127.0.0.1:8001
```

## 관리형 sidecar lifecycle

VibeVoice repository와 streaming checkpoint가 이미 준비된 환경에서는 LangTextFlow가 해당 sidecar의 **start / readiness / stop lifecycle**을 관리할 수 있습니다.

```env
LANGTEXTFLOW_VIBEVOICE_URL=http://127.0.0.1:8001
LANGTEXTFLOW_VIBEVOICE_REPO_PATH=/path/to/VibeVoice
LANGTEXTFLOW_VIBEVOICE_PYTHON=/path/to/python
LANGTEXTFLOW_VIBEVOICE_MODEL_PATH=/path/to/prepared-streaming-checkpoint
```

선택 설정:

```env
LANGTEXTFLOW_VIBEVOICE_TENSOR_PARALLEL_SIZE=1
LANGTEXTFLOW_VIBEVOICE_MAX_MODEL_LEN=16384
LANGTEXTFLOW_VIBEVOICE_MAX_AUDIO_WINDOWS=512
LANGTEXTFLOW_VIBEVOICE_MM_PROCESSOR_CACHE_GB=16.0
LANGTEXTFLOW_VIBEVOICE_GPU_MEMORY_UTILIZATION=0.85
LANGTEXTFLOW_VIBEVOICE_STARTUP_TIMEOUT_SECONDS=600.0
```

관리형 lifecycle은 공식 installer launcher를 호출하지 않고 준비된 환경에서 직접 다음 server module만 실행합니다.

```text
python -m vllm_plugin.asr_streaming_server ...
```

시작 전 다음을 검증합니다.

- 관리 주소가 `localhost` 또는 loopback IP인지
- VibeVoice repository가 존재하는지
- `vllm_plugin/asr_streaming_server.py`가 존재하는지
- streaming checkpoint가 로컬에 존재하는지
- `preprocessor_config.json`이 존재하는지
- `added_tokens.json`에 `<|text_chunk_end|>`가 준비되어 있는지
- 지정 Python executable이 실제로 존재하는지

운영자 전용 lifecycle API:

```text
GET  /api/v1/setup/vibevoice
POST /api/v1/setup/vibevoice/start
POST /api/v1/setup/vibevoice/stop
```

UI는 lifecycle을 다음 상태로 구분합니다.

- `unconfigured`: 관리형 실행에 필요한 로컬 경로가 준비되지 않음
- `stopped`: 관리형 sidecar를 시작할 수 있음
- `starting`: 프로세스 시작 후 `/v1/config` readiness를 기다리는 중
- `managed`: LangTextFlow가 직접 시작했고 READY인 sidecar
- `external`: 다른 프로세스가 이미 같은 endpoint에서 정상 응답 중
- `error`: 관리형 프로세스 시작 또는 health check 실패

### 프로세스 소유권 규칙

LangTextFlow는 **자신이 시작한 프로세스만 종료합니다.** `/v1/config`가 이미 응답하지만 LangTextFlow가 만든 PID가 아니면 `external`로 표시하고 `stop` 요청에서도 종료하지 않습니다.

또한 활성 자막 세션이 실행 중인 동안에는 lifecycle API를 통한 managed VibeVoice 종료를 차단합니다. 서버가 종료될 때는 먼저 caption runtime을 정리한 뒤 LangTextFlow가 소유한 sidecar만 종료합니다.

### 자동화하지 않는 작업

현재 backend lifecycle manager는 다음 작업을 수행하지 않습니다.

- Git repository clone/update
- Python package 설치
- CUDA / GPU driver 설치
- FFmpeg 등 시스템 package 설치
- VibeVoice model weight 다운로드
- checkpoint tokenizer mutation

이 작업들은 추후 desktop installer/runtime provisioning 단계에서 명시적 사용자 동의, 진행률, 검증, 복구 절차와 함께 구현합니다.

## Context / Hotwords

운영자 UI에서 입력한 세션 이름, 발표자, 설명, hotwords, 활성화된 glossary 항목은 VibeVoice 세션의 `context_info`에 전달됩니다.

예:

```text
Mission Conference | John Smith | 요한복음, 로마서, 칭의, 의롭다 하심
```

이 계층은 이후 correction/translation에도 동일한 context store를 재사용하도록 설계합니다.

## 오디오 포맷

LangTextFlow의 브라우저 클라이언트는 VibeVoice `/v1/config`의 sample rate에 맞춘 `AudioContext`를 생성하고 다음 포맷으로 전송합니다.

- mono
- little-endian float32 PCM
- 약 250ms 단위 frame
- WebSocket binary frame

backend는 frame을 bounded queue에 넣어 provider에 전달합니다. queue가 가득 차면 producer에 backpressure가 걸리므로 오디오를 조용히 버리지 않습니다.

## 현재 검증 범위

CI에서 다음을 검증합니다.

- provider URL 변환
- session context/hotword 전달
- `/v1/config` 계약
- WebSocket 초기 설정 메시지
- binary PCM 전달
- chunk transcript -> `STABLE` LangTextFlow segment 변환
- managed lifecycle command에 installer/pip가 포함되지 않음
- 외부 sidecar 소유권 보호
- managed process start/readiness/stop
- non-loopback managed start 차단
- streaming tokenizer 준비 여부 검증
- backend lint/test
- frontend production build

실제 7B 모델을 사용한 GPU inference 품질/RTF/장시간 안정성은 별도의 hardware benchmark가 필요합니다. 이 결과는 향후 `docs/benchmarks/` 아래에 기록할 예정입니다.

## 운영상 중요한 제한

VibeVoice 공식 streaming server는 세션별로 누적 context/KV cache를 유지합니다. 장시간 집회에서는 모델의 `max_model_len`, `max_audio_windows`, multimodal cache 설정에 따라 세션 한도가 발생할 수 있습니다. 따라서 LangTextFlow의 상용 수준 완료 전에는 30/60/90분 soak test와 자동 session rollover 전략을 검증해야 합니다.
