# LangTextFlow Architecture

## 1. 목표

LangTextFlow의 핵심은 특정 ASR 모델이 아니라 **실시간 자막 상태를 안정적으로 관리하는 파이프라인**입니다. ASR, 교정, 번역은 교체 가능해야 하며 UI는 동일한 이벤트 계약만 소비합니다.

## 2. Event pipeline

```text
Audio input
   │
   ▼
Audio buffer / VAD
   │
   ▼
ASR adapter ─────── VibeVoice / faster-whisper / sherpa-onnx
   │
   ▼
PARTIAL
   │
Transcript stabilizer
   ▼
STABLE
   │
Deterministic normalizer + glossary + constrained corrector
   ▼
CORRECTED
   │
Translator(s)
   ▼
TRANSLATED
   │
Commit policy
   ▼
COMMITTED
   │
WebSocket fan-out
   ├─ Operator UI
   ├─ Projector
   ├─ OBS Browser Source
   └─ Audience mobile web
```

## 3. Segment invariants

모든 자막은 `segment_id`와 증가하는 `version`을 갖습니다.

- 같은 segment의 version은 반드시 증가합니다.
- stage는 역행할 수 없습니다.
- `COMMITTED` segment는 화면에서 다시 수정하지 않습니다.
- 후처리/번역 실패는 원문 segment 자체를 잃게 만들면 안 됩니다.
- 프론트엔드는 문자열 append가 아니라 `(segment_id, version)` patch를 적용합니다.

## 4. ASR adapter

모든 엔진은 동일 인터페이스를 구현합니다.

```python
class AsrEngine(ABC):
    async def start(self, request): ...
    async def stop(self): ...
    @property
    def running(self): ...
```

P1에서 `VibeVoiceStreamingEngine`과 `FasterWhisperEngine`을 추가합니다. 실제 오디오 callback과 추론 worker 사이에는 bounded queue/ring buffer를 두어 캡처 thread가 inference에 의해 block되지 않게 합니다.

## 5. 제품 경계

### Server
- audio/session lifecycle
- ASR/correction/translation orchestration
- caption state machine
- WebSocket distribution
- persistent session/log storage (P2)

### Operator Web
- 시작/중지
- 입력 장치 선택
- 입력/출력 언어
- 모델/성능 preset
- 용어집
- 상태/오류/지연 모니터링

### Audience Web
- 언어 선택
- 큰 글씨 자막
- QR 진입
- 이전 1~2 segment 열람

## 6. Reference implementation policy

`XWHQSJ/captionflow`의 low-latency partial/final 구조와 audio/inference 분리 패턴은 설계 참고 대상으로만 사용합니다. GPL 소스 코드를 복사하거나 파생 구현으로 포함하지 않습니다.
