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

## VibeVoice sidecar 실행

Microsoft VibeVoice 저장소의 streaming launcher를 사용합니다. LangTextFlow backend가 기본적으로 `8000` 포트를 사용하므로 VibeVoice는 `8001` 포트를 권장합니다.

```bash
git clone https://github.com/microsoft/VibeVoice.git
cd VibeVoice
python3 vllm_plugin/scripts/start_streaming_server.py \
  --model microsoft/VibeVoice-ASR-Streaming-7B \
  --port 8001
```

이미 의존성이 준비된 환경에서는 공식 launcher의 `--skip-deps` 옵션을 사용할 수 있습니다.

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
- backend lint/test
- frontend production build

실제 7B 모델을 사용한 GPU inference 품질/RTF/장시간 안정성은 별도의 hardware benchmark가 필요합니다. 이 결과는 향후 `docs/benchmarks/` 아래에 기록할 예정입니다.

## 운영상 중요한 제한

VibeVoice 공식 streaming server는 세션별로 누적 context/KV cache를 유지합니다. 장시간 집회에서는 모델의 `max_model_len`, `max_audio_windows`, multimodal cache 설정에 따라 세션 한도가 발생할 수 있습니다. 따라서 LangTextFlow의 상용 수준 완료 전에는 30/60/90분 soak test와 자동 session rollover 전략을 검증해야 합니다.
