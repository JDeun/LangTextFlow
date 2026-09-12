# Realtime observability

LangTextFlow의 실시간 자막 경로는 **라이브 송출을 우선**하고, 운영자가 병목과 provider 장애를 즉시 찾을 수 있도록 별도의 telemetry를 제공합니다.

## 기본 원칙

현재 VAD는 음성 프레임을 제거하는 gate가 아닙니다. 브라우저에서 받은 PCM은 그대로 ASR provider에 전달하며, RMS 기반 `EnergyVad`는 운영자 화면의 입력 상태 표시와 현장 진단에만 사용합니다.

이 방식은 교회·강연장처럼 잔향이 크고 조용한 발화가 존재하는 환경에서 초기 단계의 공격적인 VAD가 문장 첫 음절이나 작은 목소리를 잘라내는 위험을 피하기 위한 선택입니다.

또한 `session.running`과 ASR provider의 실제 health는 별도로 관측합니다. 세션 객체가 실행 중이어도 provider worker가 죽으면 운영자 UI는 `FAILED`로 표시되어야 합니다.

## Metrics

`GET /api/v1/metrics`는 operator loopback 전용 endpoint입니다.

### ASR provider health

- `asr_provider`: 현재 실제 활성 ASR provider
- `asr_running`: provider가 현재 오디오를 처리할 수 있는 상태인지 여부
- `asr_failure`: provider 내부 worker/socket에서 관측된 fatal failure 메시지. 정상일 때 `null`

VibeVoice는 WebSocket sender/receiver의 예기치 않은 종료를 failure로 기록합니다. faster-whisper는 micro-batch inference worker 예외를 failure로 기록합니다. `Auto` startup fallback wrapper는 현재 active provider의 health/failure를 그대로 노출합니다.

현재 자동 fallback은 **세션 시작 시점에만** 수행합니다. 세션 도중 provider failure가 감지되면 상태를 숨기지 않고 즉시 `FAILED`로 표시하지만, replay buffer를 이용한 seamless provider 전환은 별도 reliability 단계입니다.

### Audio input

- `audio_frames_received`: backend가 받은 PCM frame 수
- `audio_bytes_received`: 누적 PCM byte 수
- `audio_duration_ms`: sample rate 기준 누적 오디오 길이
- `audio_rms_dbfs`: 마지막 frame의 RMS dBFS
- `voice_active`: energy threshold + hangover 기준 activity 상태
- `last_audio_enqueue_wait_ms`: ASR provider queue에 frame을 넣는 데 걸린 시간
- `audio_backpressure_events`: enqueue가 경고 threshold를 넘은 횟수

### Queue pressure

- `asr_queue_depth / asr_queue_capacity`: ASR provider의 현재 audio queue
- `asr_queue_high_watermark`: 세션 중 관측된 최대 ASR queue depth
- `postprocess_queue_depth / postprocess_queue_capacity`: correction/translation worker queue
- `persistence_queue_depth / persistence_queue_capacity`: SQLite transcript writer queue

Queue가 지속적으로 차오르면 downstream 처리량이 realtime 입력량을 따라가지 못하고 있다는 의미입니다.

### Latency

- `last_asr_lag_ms`: 해당 segment의 오디오 종료 시점부터 `STABLE` 이벤트까지
- `last_correction_latency_ms`: `STABLE → CORRECTED`
- `last_translation_latency_ms`: `CORRECTED → TRANSLATED`
- `last_commit_latency_ms`: `STABLE → COMMITTED`

각 값은 동일 `segment_id`의 stage timestamp를 비교해 계산합니다. 따라서 correction과 translation 병목을 ASR 지연과 분리해서 볼 수 있습니다.

## 운영자 상태 표시

- `LIVE`: 세션 실행 중이며 ASR provider가 healthy/running
- `STARTING`: 세션은 실행 중이지만 provider가 아직 running으로 확인되지 않음
- `FAILED`: provider가 fatal failure를 보고함
- `IDLE`: 세션이 실행 중이지 않음

`FAILED` 상태에서는 최근 failure 문자열을 provider 이름과 함께 표시합니다. 운영자가 단순 무음과 ASR 장애를 구분할 수 있게 하는 것이 목적입니다.

## 기본 운영자 UI 경고 기준

UI 색상은 진단용 휴리스틱이며 품질 SLA가 아닙니다.

| Metric | Warning | Danger |
| --- | ---: | ---: |
| ASR queue | 50% | 80% |
| Postprocess queue | 50% | 80% |
| Persistence queue | 50% | 80% |
| Audio enqueue | 50 ms | 200 ms |
| ASR lag | 1.5 s | 3.0 s |
| Correction | 250 ms | 800 ms |
| Translation | 1.0 s | 2.5 s |
| Stable → commit | 1.5 s | 3.0 s |

실제 임계값은 현장 benchmark와 30/60/90분 soak test 결과를 기반으로 조정해야 합니다.

## Environment settings

```env
LANGTEXTFLOW_VAD_THRESHOLD_DBFS=-45.0
LANGTEXTFLOW_VAD_HANGOVER_FRAMES=3
LANGTEXTFLOW_AUDIO_BACKPRESSURE_WARN_MS=50.0
```

`VAD_THRESHOLD_DBFS`는 공간·마이크·PA leakage에 따라 달라질 수 있습니다. 운영 전 sound check에서 `audio_rms_dbfs`를 확인해 조정하는 것이 좋습니다.

## 다음 단계

- replay buffer + duplicate suppression 기반 mid-session provider failover
- reconnect attempt/success/failure counter
- 실제 한국어/영어 집회 샘플로 threshold calibration
- 30/60/90분 soak test에서 queue high-watermark와 메모리 추적
- 필요 시 Silero/WebRTC 계열 semantic VAD 비교 benchmark
- metrics time-series 저장 및 세션별 진단 report
