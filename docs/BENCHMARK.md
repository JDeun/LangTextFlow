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

### 로컬 M2M100 두 대상 언어 평가

[고정 revision 모델 준비 절차](local-m2m100-provisioning.md)로 준비한 CT2/tokenizer
캐시와 기존 제품 설정을 그대로 사용합니다. runtime은 모델을 다운로드하지 않습니다.

```bash
export LANGTEXTFLOW_LOCAL_M2M100_CT2_DIR=/path/to/prepared/m2m100-ct2
export LANGTEXTFLOW_LOCAL_M2M100_TOKENIZER_DIR=/path/to/prepared/m2m100-tokenizer
export LANGTEXTFLOW_LOCAL_M2M100_CPU_THREADS=2
langtextflow-runtime-benchmark fixture.wav \
  --engine faster-whisper --source-language ko \
  --target-language en --target-language ja \
  --translation-provider m2m100-ct2 --preset church \
  --pace realtime --duration-minutes 30 \
  --output benchmark-results/runtime-m2m100-ko-en-ja-30m.json
```

ASR도 준비된 로컬 캐시를 사용해야 합니다. `--translation-model`은 기존 LLM provider의
모델 선택 옵션이며 M2M100 캐시 경로를 바꾸지 않습니다. `resource_profile=low`이면
제품과 동일하게 `LANGTEXTFLOW_LOW_RESOURCE_CPU_THREADS`가 M2M100 스레드 수를 결정합니다.

`translation`에는 provider 준비 상태와 `failed_target_count`(committed caption × 대상 언어 중
available이 아닌 건수)를 기록합니다. 선택한 MT가 준비되지 않았거나 대상 번역이 실패하면
`status="failed"`, 종료 코드 1입니다. drain timeout도 종료 코드 1, 실행 예외는 2입니다.
원문만 보존된 실행을 번역 성공으로 해석하지 않습니다. 준비 상태/성공 출력은 번역 정확도나
실기기 승인과 별개이며, 이 명령 예시는 실제 30분 실행 결과가 아닙니다.

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

`memory.rss_peak_mb`, `rss_growth_mb`, `samples[].rss_mb`는 계속 benchmark
Python **부모 프로세스 RSS**입니다. 기존 결과와 의미를 바꾸지 않습니다.
`scripts/evaluate_live_fleurs_translation.py`의 `python_process_rss`도 같은
MemoryTracker를 쓰므로 이 안에 다음 별도 지표가 포함됩니다.

- `process_tree`: 부모와 재귀 자손의 **동시 샘플 RSS 합**. 네이티브 M2M100처럼
  benchmark가 소유한 자식이 모델을 로드하면 그 RSS도 포함합니다.
- `method=psutil_sum_sampled_rss_not_pss`, OS, MiB 단위, 샘플 간격을 기록합니다.
  공유 페이지는 프로세스마다 중복 계산하므로 실제 물리 RAM/PSS가 아닙니다.
  샘플 사이에 사라진 프로세스나 일시적인 peak는 놓칠 수 있습니다.
- 사라진/접근 불가 프로세스 또는 64개 탐색 상한에 걸린 샘플은 `complete=false`.
  `rss_peak_mb`는 완전히 읽은 샘플의 최대값이며, 부분 합의 최대값은
  `observed_rss_peak_mb`로 분리합니다. 비교 전 전체 `complete`와 오류 개수도 확인합니다.
- 시계열은 최근 4,096개만 보존하지만 peak/growth 집계는 모든 샘플을 반영합니다.
  부모 조회 실패/psutil 미설치는 benchmark를 중단하지 않고 가용성과 오류를 남깁니다.
- 별도 실행된 Ollama/VibeVoice/Docker 서비스, 브라우저와 GPU VRAM은 포함하지 않습니다.
  해당 서비스의 RSS/컨테이너 메모리/VRAM은 같은 시각에 별도로 수집해야 합니다.

스레드 기반 이전 실행의 부모 RSS와 새 child 실행의 부모 RSS를 총량처럼 비교하지
마세요. 같은 sampler/간격/OS/프로세스 구성으로 다시 실행한 process-tree 지표를
사용하고 외부 서비스 메모리를 따로 제시합니다. 이는 `field_ready=false`인
연구 측정이며 4GB/8GB 실기기 승인이나 장시간 안정성 증명이 아닙니다.

## 제한된 동일 입력 후보 비교 실행 계획

새 모델을 내려받기 전에 기존 캐시와 권리 검토된 revision/SHA manifest를 확인합니다.
입력은 pinned FLEURS revision `fdd74f7cdf01fc7641ab3072b6a38d364b3667f6`
(CC BY 4.0)의 같은 한국어 3개 clip으로 고정합니다. 다음 기존 ASR 하네스는
PCM SHA-256, 순서, 원문 reference, duration이 일치하지 않으면 비교를 거부합니다.
이미 확인한 SenseVoice/큰 OPUS 음성·품질 실패를 재다운로드해 반복하지 않습니다.

```bash
# 이미 준비된 모델·FLEURS 데이터 캐시만 사용. Linux의 같은 CPU runner에서 실행.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
for candidate in qwen3-0.6b zipformer-ko whispercpp-base; do
  timeout 600 python -m scripts.benchmark_cpu_asr_candidates \
    --candidate "$candidate" --model-dir "$ASR_CACHE/$candidate" \
    --threads 2 --pace realtime --whisper-cli "$WHISPER_CPP" \
    --output "artifacts/fair-asr/$candidate.json"
done
python -m scripts.compare_cpu_asr_candidates \
  artifacts/fair-asr/qwen3-0.6b.json artifacts/fair-asr/zipformer-ko.json \
  artifacts/fair-asr/whispercpp-base.json --output artifacts/fair-asr/comparison.json
```

1. 위 ASR 비교는 source CER/WER, cold load와 inference/전체 RTF를 같은 하네스로
   계산합니다. 기존 challenger의 `resources.rss_peak_mb`는 부모+자손 합이며 새
   MemoryTracker의 부모 RSS와 혼합하지 않습니다. first-caption/p95와 명시적으로
   분리된 부모/자손 RSS는 제품 runtime 하네스에서 추가 측정해야 합니다.
   Whisper.cpp와 production faster-whisper는 서로 다른
   실행 backend이므로 명칭을 섞지 않습니다. production small을 추가할 때는 동일
   WAV/reference를 `langtextflow-benchmark`에 전달하고 CPU thread/청크/힌트 설정까지
   기록합니다. utterance replay가 streaming 성능을 입증하지는 않습니다.
2. MT 단독 비교는 `church_translation_quality.v1.jsonl`에서 같은 **16개 KO→EN**
   row만 사용합니다. 기존 `compare_church_cpu_pipeline.text_benchmark`를 공통 scorer로
   재사용하면 OPUS-small의 `CTranslateMarian.translate`, M2M100의 고정 `ko/en` adapter,
   TranslateGemma의 고정 `SessionContext(preset="church")` adapter에 같은 문자열을
   넣어 같은 chrF++/용어/유효 출력/지연 지표를 계산할 수 있습니다. 각 후보는 별도
   프로세스에서 순차 실행하고 1회 cold + 3회 warm, 같은 순서와 glossary OFF/ON
   snapshot을 기록합니다. 현재 별개 MT CLI 결과는 corpus와 glossary 개입이 달라
   서로 바로 순위를 매기지 않습니다. 공통 adapter 실행 전 이 차이를 없애야 합니다.
3. 6방향은 별도 `lecture_multilingual_ko_en_ja.v1.jsonl`의 동일 8개 문장과
   `church_glossary_multilingual.v1.json`으로 M2M100/지원 LLM만 비교합니다.
   KO→EN 전용 OPUS-small을 나머지 5방향 후보로 표시하지 않습니다.
   두 텍스트 fixture의 reference는 **synthetic_unreviewed**이며, FLEURS의 다른 언어 ID를
   번역 정답으로 조합하지 않습니다. 이 단계의 점수는 인간 검수된 신학적 정확성이나
   실제 음성의 정렬된 번역 품질을 의미하지 않습니다.

후보마다 fixture/model/source SHA, 실제 성공/실패 건수, directed pair, CPU/스레드,
청크·deadline·glossary·sampler 설정을 함께 보존합니다. 실패를 제외한 평균만으로
승자를 고르지 않으며, 동일 입력/측정 방식이 확보되기 전에는 최적 조합을 주장하지 않습니다.
