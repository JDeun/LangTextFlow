# Benchmark / soak test protocol

LangTextFlow의 실시간 품질과 안정성을 같은 조건에서 반복 측정하기 위한 기준입니다. 현재 harness는 목적이 다른 두 층으로 나뉩니다.

- `langtextflow-benchmark`: ASR provider만 직접 측정하는 micro-benchmark
- `langtextflow-runtime-benchmark`: 실제 `CaptionRuntime` 전체를 통과하는 product benchmark

두 결과를 섞어서 해석하지 않습니다. 모델 자체의 성능 비교는 ASR benchmark를, 실제 제품 배포 판단은 runtime benchmark를 우선합니다.

## 공통 입력 fixture

재현성을 위해 입력은 다음 규격으로 고정합니다.

- WAV
- mono
- signed PCM16
- ASR provider와 동일한 sample rate. 현재 기본 환경은 16 kHz

다른 파일은 `ffmpeg`로 변환합니다.

```bash
ffmpeg -i input.m4a -ac 1 -ar 16000 -c:a pcm_s16le fixture.wav
```

정확도 평가까지 할 경우 사람이 검수한 UTF-8 reference transcript를 함께 준비합니다.

```text
fixture.wav
fixture.txt
```

## 설치

faster-whisper와 RSS 측정을 함께 사용할 경우:

```bash
pip install -e '.[whisper,benchmark]'
```

VibeVoice만 측정할 경우:

```bash
pip install -e '.[benchmark]'
```

`psutil`이 없더라도 benchmark 자체는 실행됩니다. 이 경우 `memory.available=false`로 기록됩니다.

# 1. ASR micro-benchmark

`langtextflow-benchmark`는 UI, correction, translation, SQLite를 우회하고 실제 ASR provider contract만 사용합니다. 브라우저/후처리 비용과 ASR 자체 병목을 분리할 때 사용합니다.

기록 항목:

- provider startup 시간
- first STABLE / p50 / p95 / max ASR lag
- `max` 모드 throughput RTF
- ASR queue high-watermark / capacity
- `Auto` failover 횟수와 직전 원인
- timestamp overlap 후보
- 인접 exact duplicate 후보
- reference가 있을 때 CER / WER
- Python process RSS peak / growth / time-series
- raw ASR transcript와 segment timestamp

### 실제 시간 latency

```bash
langtextflow-benchmark fixture.wav \
  --engine auto \
  --source-language ko \
  --pace realtime \
  --reference fixture.txt \
  --output benchmark-results/asr-auto-realtime.json
```

VibeVoice와 faster-whisper를 각각 고정해 같은 fixture로 비교할 수 있습니다.

```bash
--engine vibevoice
--engine faster-whisper
```

`realtime_lag_*`는 benchmark 시작 이후 실제 경과 시간에서 segment의 audio `end_ms`를 뺀 값이므로 `--pace realtime`에서만 의미가 있습니다.

### 최대 처리량 / RTF

```bash
langtextflow-benchmark fixture.wav \
  --engine faster-whisper \
  --pace max \
  --output benchmark-results/asr-fw-max.json
```

`throughput_rtf < 1.0`이면 오디오 1초를 1초보다 빠르게 처리하는 것입니다. 짧은 fixture는 startup/drain 비중이 크므로 최소 수 분짜리 샘플을 권장합니다.

# 2. Full runtime benchmark

`langtextflow-runtime-benchmark`는 실제 제품 경로를 사용합니다.

```text
WAV
 → CaptionRuntime
 → ASR / failover
 → correction
 → translation(optional)
 → COMMITTED caption
 → SQLite persistence
```

따라서 correction/translation backlog, persistence pressure, failover 이후 committed caption 연속성까지 같이 측정합니다.

```bash
langtextflow-runtime-benchmark fixture.wav \
  --engine auto \
  --source-language ko \
  --target-language en \
  --translation-provider ollama \
  --preset church \
  --pace realtime \
  --reference fixture.txt \
  --hotword 요한복음 \
  --hotword 칭의 \
  --output benchmark-results/runtime-auto-ko-en.json
```

추가 기록 항목:

- ASR latency summary
- `STABLE → CORRECTED`
- `CORRECTED → TRANSLATED`
- `STABLE → COMMITTED`
- ASR / postprocess / persistence queue peak
- audio backpressure count
- failover 발생 audio timeline 위치
- failover 전후 committed caption gap
- committed caption exact duplicate rate
- 전체 caption timeline 최대 gap
- persistence error

runtime benchmark는 로컬 임시 SQLite DB를 사용하므로 일반 사용자 세션 기록을 오염시키지 않습니다.

# 3. Pace

## `--pace realtime`

실제 캡처처럼 audio 시간축에 맞춰 chunk를 공급합니다.

다음을 평가합니다.

- 사용자 체감 ASR 지연
- 번역/교정이 실시간 입력을 따라가는지
- queue가 장시간 누적되는지
- failover 시 자막 공백
- memory 장기 안정성

## `--pace max`

sleep 없이 가능한 한 빠르게 공급합니다.

다음을 평가합니다.

- 최대 처리량
- RTF
- queue/backpressure 한계
- 장비 간 headroom 비교

`max` 결과를 실제 사용자 latency로 해석하면 안 됩니다.

# 4. 30 / 60 / 90분 soak test

`--duration-minutes`를 지정하면 같은 fixture를 반복 재생해 목표 길이까지 공급합니다.

제품 안정성 검증은 full runtime harness를 기준으로 합니다.

```bash
langtextflow-runtime-benchmark fixture.wav \
  --engine auto \
  --source-language ko \
  --target-language en \
  --translation-provider ollama \
  --pace realtime \
  --duration-minutes 30 \
  --output benchmark-results/runtime-soak-30m.json
```

동일 조건에서 `30`, `60`, `90`을 순차 실행합니다.

### 상용 릴리스 전 최소 매트릭스

| Scenario | Duration | 확인 항목 |
| --- | ---: | --- |
| KO source caption | 30 min | crash/fatal error 0 |
| KO → EN | 60 min | translation/postprocess queue 누적 없음 |
| EN → KO | 60 min | translation/postprocess queue 누적 없음 |
| Auto forced failover | 30 min | session 유지 + fallback 성공 |
| Production candidate | 90 min | latency/memory trend 안정 |

한국어와 영어 각각 실제 교회/강연장 샘플로 수행합니다.

### Soak test에서 반드시 볼 항목

- `status == "ok"`
- fatal ASR failure가 남지 않는지
- `persistence_error == null`
- drain timeout이 발생하지 않는지
- queue high-watermark가 capacity에 계속 붙지 않는지
- p95 latency가 시간 경과에 따라 누적 증가하지 않는지
- RSS가 지속적으로 단조 증가하지 않는지
- duplicate/timeline gap이 세션 길이에 따라 악화되지 않는지
- `Auto`에서 예상하지 않은 failover가 반복되지 않는지

RSS는 단일 peak보다 time-series 추세가 중요합니다. 모델 초기 로딩 때문에 초반 상승은 정상일 수 있지만 30→60→90분 동안 계속 선형 증가하면 leak 가능성을 조사합니다.

# 5. Failover field test

unit test 외에 실제 provider handoff를 검증해야 합니다. `Auto + realtime` 실행 중 VibeVoice sidecar를 의도적으로 종료합니다.

확인 항목:

1. failover count가 1 증가하는지
2. 최종 provider가 faster-whisper인지
3. fatal failure 없이 세션이 계속되는지
4. provider-local timestamp가 global timeline에서 역행하지 않는지
5. replay 구간 중복 자막이 억제되는지
6. runtime report의 `failover.caption_gap_ms`가 허용 범위인지
7. fallback cold-start 때문에 queue가 장시간 포화되지 않는지

ASR micro-benchmark의 `segments`에는 `auto-00-*`, `auto-01-*` generation ID와 global timestamp가 남아 handoff 경계를 직접 분석할 수 있습니다.

# 6. 정확도 해석

## 한국어

띄어쓰기에 따라 WER가 크게 흔들리므로 **CER를 주 지표**로 봅니다. 현재 CER는 NFKC/case normalization 후 whitespace를 제외한 character sequence를 비교합니다.

## 영어

WER와 CER를 함께 봅니다.

## 현장 중요 오류

전체 CER/WER 외에 다음 fixture를 별도로 둡니다.

- 성경 책 이름과 장절
- 침례, 만찬, 구속, 칭의, 성화 등 교회/신학 용어
- 선교사·교회·지명 고유명사
- 숫자, 날짜, 인원 수
- 한국어↔영어 code-switching
- 부정 표현

전체 CER가 낮아도 이 오류는 현장 자막 품질에 치명적일 수 있습니다.

# 7. 권장 fixture set

| Fixture | 목적 |
| --- | --- |
| Clean speech | provider baseline |
| Church / theology | glossary/domain term 정확도 |
| PA / reverberant room | 실제 예배당 음향 |
| Code-switch / proper nouns | 선교사 이름, 성경명, 영어 혼용 |

각 fixture는 가능하면 5~15분으로 만들고 모든 provider/model 조합에서 동일 원본을 사용합니다.

# 8. 초기 합격 gate

다음 값은 최종 SLA가 아니라 field benchmark 시작용 보수적 기준입니다.

- fatal ASR failure: 0 (fallback provider까지 소진된 경우 제외)
- persistence error: 0
- audio backpressure가 지속적으로 증가하지 않을 것
- ASR queue가 capacity 80% 이상에 장시간 머물지 않을 것
- realtime ASR lag p95: 3초 미만 목표
- stable → commit p95: 3초 미만 목표
- failover caption gap: 2초 미만 목표 후 현장 데이터로 재조정
- exact committed duplicate rate: 1% 미만 목표
- 90분 RSS가 지속적으로 단조 증가하지 않을 것

실측 결과가 쌓이면 장비 class와 provider별 SLA로 분리합니다.

# 9. 결과와 개인정보

`benchmark-results/`와 `benchmarks/results/`는 Git에서 제외됩니다. 실제 녹음에는 개인정보나 설교/간증 내용이 포함될 수 있으므로 원본 WAV와 raw transcript를 공개 저장소에 commit하지 않습니다.

공개 가능한 결과만 익명화·집계한 뒤 문서나 release artifact로 올립니다.

`memory.*`는 benchmark CLI가 실행되는 backend Python process의 RSS입니다.

- faster-whisper: 같은 Python process이므로 의미가 큼
- VibeVoice: 별도 sidecar process/GPU memory는 포함되지 않음
- GPU VRAM: 현재 harness에서 직접 수집하지 않음

최종 hardware benchmark에서는 NVIDIA VRAM과 VibeVoice sidecar RSS도 별도로 기록합니다.
