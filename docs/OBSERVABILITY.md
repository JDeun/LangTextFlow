# Realtime observability

LangTextFlow의 실시간 자막 경로는 **라이브 송출을 우선**하고, 운영자가 병목을 즉시 찾을 수 있도록 별도의 telemetry를 제공합니다.

## 기본 원칙

현재 VAD는 음성 프레임을 제거하는 gate가 아닙니다. 브라우저에서 받은 PCM은 그대로 ASR provider에 전달하며, RMS 기반 `EnergyVad`는 운영자 화면의 입력 상태 표시와 현장 진단에만 사용합니다.

이 방식은 교회·강연장처럼 잔향이 크고 조용한 발화가 존재하는 환경에서 초기 단계의 공격적인 VAD가 문장 첫 음절이나 작은 목소리를 잘라내는 위험을 피하기 위한 선택입니다.

## Metrics

`GET /api/v1/metrics`는 operator loopback 전용 endpoint입니다.

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

- 실제 한국어/영어 집회 샘플로 threshold calibration
- 30/60/90분 soak test에서 queue high-watermark와 메모리 추적
- provider reconnect / failure counter
- 필요 시 Silero/WebRTC 계열 semantic VAD 비교 benchmark
- metrics time-series 저장 및 세션별 진단 report
