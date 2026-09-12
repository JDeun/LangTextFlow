# ASR benchmark / soak test protocol

LangTextFlow의 실시간 ASR 품질과 안정성을 같은 조건에서 반복 측정하기 위한 기준입니다. `langtextflow-benchmark` CLI는 UI를 통하지 않고 실제 ASR provider contract를 직접 사용하므로 브라우저 렌더링 비용과 ASR 자체 병목을 분리해서 볼 수 있습니다.

## 측정 대상

현재 harness가 기록하는 주요 항목은 다음과 같습니다.

- provider startup 시간
- 입력 오디오 길이와 전체 처리 시간
- `realtime` 모드의 first STABLE / p50 / p95 / max ASR lag
- `max` 모드의 throughput RTF
- ASR queue high-watermark / capacity
- `Auto` provider failover 횟수와 직전 원인
- STABLE segment timestamp overlap 후보
- 인접 exact duplicate 자막 후보
- reference transcript가 있을 때 CER / WER
- backend Python process의 RSS peak / growth / time-series
- 최종 raw ASR transcript와 segment별 timestamp

결과는 JSON으로 저장할 수 있어 장비, 모델, 설정, 날짜별 비교가 가능합니다.

## 설치

faster-whisper와 메모리 측정을 함께 사용할 경우:

```bash
pip install -e '.[whisper,benchmark]'
```

VibeVoice만 측정할 경우 `whisper` extra는 필요하지 않지만 RSS time-series를 원하면 `benchmark` extra를 설치합니다.

```bash
pip install -e '.[benchmark]'
```

`psutil`이 없더라도 benchmark 자체는 실행됩니다. 이 경우 `memory.available=false`로 기록됩니다.

## 오디오 fixture 규격

재현성을 위해 benchmark 입력은 다음 규격으로 고정합니다.

- WAV
- mono
- 16 kHz
- signed PCM16

다른 파일은 `ffmpeg`로 변환합니다.

```bash
ffmpeg -i input.m4a -ac 1 -ar 16000 -c:a pcm_s16le fixture.wav
```

실제 교회/강연장 평가에서는 원본 녹음 파일도 별도로 보존하고, benchmark용 변환본만 이 규격으로 만듭니다.

## 1. 실시간 latency benchmark

실제 캡처 속도처럼 100 ms PCM chunk를 시간에 맞춰 공급합니다.

```bash
langtextflow-benchmark fixture.wav \
  --engine faster-whisper \
  --source-language ko \
  --pace realtime \
  --reference fixture.txt \
  --output benchmark-results/fw-ko-realtime.json
```

VibeVoice:

```bash
langtextflow-benchmark fixture.wav \
  --engine vibevoice \
  --source-language ko \
  --pace realtime \
  --reference fixture.txt \
  --output benchmark-results/vibe-ko-realtime.json
```

Auto mode:

```bash
langtextflow-benchmark fixture.wav \
  --engine auto \
  --source-language ko \
  --pace realtime \
  --reference fixture.txt \
  --output benchmark-results/auto-ko-realtime.json
```

`realtime_lag_*`는 benchmark 시작 이후 실제 경과 시간에서 segment의 오디오 `end_ms`를 뺀 값입니다. 따라서 `--pace realtime`에서만 의미가 있으며 `max` 모드에서는 `null`입니다.

## 2. 처리량 / RTF benchmark

오디오를 실제 시간보다 빠르게 공급해 provider의 최대 처리량과 queue pressure를 측정합니다.

```bash
langtextflow-benchmark fixture.wav \
  --engine faster-whisper \
  --source-language ko \
  --pace max \
  --output benchmark-results/fw-ko-throughput.json
```

`throughput_rtf < 1.0`이면 해당 조건에서 오디오 1초를 1초보다 빠르게 처리할 수 있다는 의미입니다. 다만 짧은 fixture는 startup/drain 영향이 커지므로 최소 수 분짜리 파일을 권장합니다.

## 3. 30 / 60 / 90분 soak test

`--duration-minutes`를 지정하면 같은 fixture를 반복 재생해 목표 오디오 길이까지 계속 공급합니다.

```bash
langtextflow-benchmark fixture.wav \
  --engine auto \
  --source-language ko \
  --pace realtime \
  --duration-minutes 30 \
  --output benchmark-results/auto-soak-30m.json
```

같은 방식으로 `60`, `90`을 실행합니다.

```bash
--duration-minutes 60
--duration-minutes 90
```

장시간 안정성 평가는 `pace realtime`을 기준으로 합니다. `pace max` 반복 재생은 별도의 stress test로 취급합니다.

### Soak test에서 반드시 볼 항목

- `status == "ok"`
- `provider.failure == null`
- `timing.drained_before_timeout == true`
- queue high-watermark가 capacity에 지속적으로 붙지 않는지
- p95 ASR lag가 시간 경과에 따라 누적 증가하지 않는지
- RSS가 지속적으로 단조 증가하지 않는지
- segment duplicate / timestamp overlap 비율이 세션 길이에 따라 증가하지 않는지
- `Auto`라면 예상하지 않은 failover가 발생하지 않는지

RSS는 단일 숫자보다 `memory.samples`의 추세를 봐야 합니다. 모델 초기 로딩 때문에 초반 메모리 상승은 정상일 수 있지만, 30→60→90분 동안 계속 선형 증가하면 leak 가능성을 조사해야 합니다.

## 4. Failover field test

실제 provider handoff는 unit test만으로 충분하지 않습니다. `Auto` + `realtime` benchmark를 실행한 뒤 재생 중 VibeVoice sidecar를 의도적으로 종료해 다음을 확인합니다.

1. `provider.failover_count == 1`
2. 최종 resolved provider가 faster-whisper인지
3. fatal `provider.failure`이 남지 않는지
4. failover 전후 segment timestamp가 역행하지 않는지
5. replay 구간이 대량 중복 자막으로 나타나지 않는지
6. 자막 공백(gap)이 실제 예배에서 허용 가능한 수준인지
7. fallback cold-start 때문에 queue가 장시간 포화되지 않는지

JSON의 `segments`에는 generation이 포함된 `auto-00-*`, `auto-01-*` segment ID와 global timestamp가 남기 때문에 handoff 경계를 사후 분석할 수 있습니다.

## Reference transcript

reference는 UTF-8 plain text 파일입니다. 실제 음성을 사람이 확인해 가능한 한 정확한 verbatim transcript를 만듭니다.

```text
fixture.wav
fixture.txt
```

### 한국어

한국어는 띄어쓰기에 따라 WER가 크게 흔들리므로 **CER를 주 지표**로 봅니다. 현재 CER 계산은 NFKC/case normalization 후 whitespace를 제외한 character sequence를 비교합니다.

### 영어

영어는 WER와 CER를 함께 봅니다. WER는 whitespace token 기준입니다.

### 고유명사 / 성경 용어

일반 CER/WER 외에 별도 fixture를 두는 것이 좋습니다.

- 성경 책 이름과 장절: 요한복음, 로마서, 고린도전서 등
- 교회 용어: 침례, 만찬, 구속, 칭의, 성화 등
- 선교사 / 교회 / 지명 고유명사
- 숫자, 날짜, 인원 수
- 한국어↔영어 code-switching

이 항목은 전체 CER가 낮아도 현장 자막 품질에 치명적인 오류가 될 수 있습니다.

## 권장 fixture set

최소한 다음 네 종류를 한국어/영어로 구성합니다.

| Fixture | 목적 |
| --- | --- |
| Clean speech | provider 자체 baseline |
| Church / theology | glossary와 domain term 정확도 |
| PA / reverberant room | 실제 예배당 음향 조건 |
| Code-switch / proper nouns | 선교사 이름, 성경명, 영어 혼용 |

각 fixture는 가능하면 5~15분 정도로 만들고, 같은 원본을 모든 provider/모델 조합에서 사용합니다.

## 결과 파일 관리

`benchmark-results/`와 `benchmarks/results/`는 Git에서 제외됩니다. 실제 녹음에는 개인정보나 설교/간증 내용이 포함될 수 있으므로 원본 WAV와 raw transcript를 공개 저장소에 commit하지 않습니다.

공개 가능한 결과만 익명화·집계한 뒤 문서나 release artifact로 올립니다.

## 메모리 해석 주의

`memory.*`는 **benchmark CLI가 실행되는 backend Python process의 RSS**입니다.

- faster-whisper: 모델이 같은 Python process에 로드되므로 비교적 의미가 큽니다.
- VibeVoice: sidecar가 별도 process이므로 sidecar GPU/CPU memory는 이 값에 포함되지 않습니다.
- GPU VRAM은 현재 harness가 직접 수집하지 않습니다.

따라서 최종 hardware benchmark에서는 NVIDIA 장비의 VRAM과 VibeVoice sidecar RSS를 OS/GPU telemetry와 함께 기록해야 합니다.

## 초기 비교 매트릭스

각 장비에서 같은 fixture로 최소 다음 조합을 실행합니다.

| Engine | Pace | Purpose |
| --- | --- | --- |
| VibeVoice | realtime | 주 provider latency/quality |
| faster-whisper | realtime | fallback latency/quality |
| faster-whisper | max | local throughput / RTF |
| Auto | realtime | normal operation |
| Auto + forced VibeVoice failure | realtime | failover continuity |
| Auto | 30/60/90m realtime | long-session stability |

실측 결과가 쌓인 뒤 UI warning threshold와 기본 faster-whisper model/chunk size를 이 데이터에 맞춰 조정합니다.
