# ASR mid-session failover

LangTextFlow의 `Auto` ASR 모드는 VibeVoice를 우선 사용하고, 세션 시작 실패 또는 세션 도중 fatal provider failure가 발생하면 faster-whisper로 전환할 수 있습니다.

## 동작 범위

현재 provider 순서는 고정입니다.

```text
VibeVoice → faster-whisper
```

전환은 **한 방향**입니다. 세션 도중 VibeVoice가 복구되더라도 자동 failback하지 않습니다. provider가 반복해서 왕복하면 같은 오디오가 여러 번 전사되거나 자막 순서가 깨질 위험이 있기 때문입니다.

직접 `VibeVoice` 또는 `faster-whisper` 엔진을 선택한 세션은 자동으로 다른 provider로 전환하지 않습니다. mid-session failover는 `Auto` 모드에만 적용됩니다.

## Replay buffer

`Auto` wrapper는 브라우저에서 수신한 float32 PCM의 최근 구간을 메모리 ring buffer에 보관합니다.

기본값:

```env
LANGTEXTFLOW_ASR_REPLAY_SECONDS=8.0
LANGTEXTFLOW_ASR_HEALTH_CHECK_SECONDS=0.25
```

provider failure가 감지되면 다음 순서로 처리합니다.

1. 실패한 provider를 중지합니다.
2. 다음 provider를 시작합니다.
3. 새 provider의 sample rate가 현재 오디오 stream과 같은지 확인합니다.
4. 최근 PCM ring buffer를 새 provider에 replay합니다.
5. 새 provider의 local timestamp를 session global timestamp로 rebase합니다.
6. replay 때문에 발생하는 중복 STABLE event를 억제합니다.
7. active provider를 session state/history와 telemetry에 반영합니다.

8초는 안전한 초기값입니다. 현재 faster-whisper 기본 micro-batch가 4초이므로 최소 한두 개 inference window를 다시 구성할 수 있는 여유를 둡니다. 실제 값은 현장 benchmark 결과에 따라 조정해야 합니다.

## Timestamp rebasing

각 ASR provider는 자체 세션을 새로 시작하므로 timestamp가 다시 0에서 시작합니다. wrapper는 failover 순간의 global audio cursor와 replay duration을 이용해 새 provider의 origin을 계산합니다.

```text
provider_origin_ms = global_audio_cursor_ms - replay_duration_ms

global_start_ms = provider_origin_ms + provider_start_ms
global_end_ms   = provider_origin_ms + provider_end_ms
```

따라서 downstream pipeline, telemetry, SRT/WebVTT export는 provider가 바뀌더라도 하나의 연속된 session timeline을 사용합니다.

## Duplicate suppression

Replay는 의도적으로 이미 처리된 오디오 일부를 다시 전송합니다. LangTextFlow는 두 단계로 중복을 억제합니다.

1. 새 event의 global `end_ms`가 기존 마지막 ASR output보다 과거라면 event 전체를 버립니다.
2. event가 기존 경계를 걸쳐 있고 새 텍스트 prefix가 이전 텍스트 suffix와 정확히 겹치면 그 overlap만 제거합니다.

텍스트 dedupe는 의도적으로 보수적입니다. fuzzy semantic dedupe는 비슷하지만 실제로 새로 발화된 문장을 삭제할 수 있으므로 현재는 최소 4자의 정확한 case-insensitive overlap만 제거합니다.

## Provider health monitor

`Auto` wrapper는 기본 250 ms 간격으로 active provider의 fatal `failure` 상태를 확인합니다. 따라서 오디오 frame이 새로 들어오지 않는 순간에도 background worker/socket failure를 감지해 handoff를 시작할 수 있습니다.

동시에 `feed_audio()` 경로도 provider 상태를 다시 검사합니다. health monitor보다 먼저 오류가 발생하거나 provider queue에 frame을 넣는 과정에서 예외가 발생해도 동일한 failover 경로를 사용합니다.

## Telemetry

`/api/v1/metrics`에는 다음 항목이 추가됩니다.

- `asr_failover_count`: 현재 세션에서 성공한 mid-session handoff 횟수
- `asr_last_failover_reason`: 직전 handoff를 발생시킨 provider와 failure 원인
- `asr_provider`: 현재 active provider
- `asr_running`: 현재 provider가 정상 실행 중인지 여부
- `asr_failure`: 모든 fallback이 소진된 경우의 fatal failure

운영자 UI는 성공적으로 복구된 세션을 계속 `LIVE`로 표시하되, provider 카드와 failover count를 amber warning으로 남깁니다. 즉 서비스는 계속되고 있지만 장애가 한 번 발생했다는 사실은 숨기지 않습니다.

## Failure exhaustion

마지막 provider까지 실패하면 wrapper는 더 이상 오디오를 처리하지 않고 fatal `AsrEngineError`를 발생시킵니다. 이때 telemetry는 `FAILED` 상태가 되며 failure 문자열에 원래 provider 오류와 fallback 시작 실패 원인이 포함됩니다.

## 현재 한계

- 한 세션 안에서 실패한 provider를 다시 retry/failback하지 않습니다.
- exact text overlap만 제거하며 fuzzy semantic dedupe는 하지 않습니다.
- replay buffer는 메모리 기반이며 프로세스 crash 이후 복구에는 사용할 수 없습니다.
- 실제 VibeVoice → faster-whisper 전환 품질과 gap/duplicate rate는 현장 음성 benchmark가 필요합니다.
- provider 전환 중 모델 cold-start 시간이 길면 자막 지연이 일시적으로 증가할 수 있습니다.

## 검증해야 할 현장 지표

- failover detection latency
- fallback provider startup latency
- replay completion latency
- handoff 전후 자막 gap 길이
- duplicate caption 발생률
- replay 후 WER/CER 변화
- failover 전후 queue high-watermark
- 30/60/90분 세션의 메모리 사용량
