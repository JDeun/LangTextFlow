# Translation provider benchmark

`langtextflow-translation-benchmark`는 ASR이나 전체 runtime을 거치지 않고 번역 provider 자체의 품질과 응답 지연을 동일 fixture에서 비교하는 harness입니다.

현재 비교 대상은 다음과 같습니다.

- `ollama`
- `openai-compatible` — LM Studio, vLLM 및 호환 `/v1` endpoint

이 결과는 실제 자막 end-to-end latency와 동일하지 않습니다. 실제 제품 성능은 `langtextflow-runtime-benchmark`로 별도 검증합니다.

## Fixture 형식

입력은 UTF-8 JSONL입니다. 한 줄이 하나의 번역 사례입니다.

```json
{"id":"church-ko-en-john","source_language":"ko","target_language":"en","source_text":"오늘은 요한복음 3장의 말씀을 살펴보겠습니다.","reference_text":"Today we will look at John chapter 3.","expected_terms":["John"],"context":{"preset":"church","glossary":[{"term":"요한복음","translations":{"en":"John"}}]}}
```

필수 필드:

- `id`: fixture 내 고유 ID
- `source_language`
- `target_language`
- `source_text`
- `reference_text`: 사람이 검수한 목표 번역

선택 필드:

- `expected_terms`: 출력에 반드시 나타나야 할 핵심 용어
- `context`: `SessionContext`와 동일한 구조. glossary, reference text, preset 등을 넣을 수 있음

저장소의 `benchmarks/translation.sample.jsonl`은 형식 확인용 예제입니다. 실제 성능 판단에는 교회/강연 현장에서 사람이 검수한 별도 fixture set을 사용합니다.

## 실행

Ollama:

```bash
langtextflow-translation-benchmark benchmarks/translation.sample.jsonl \
  --provider ollama \
  --model translategemma:4b \
  --repeats 5 \
  --output benchmark-results/translation-ollama.json
```

OpenAI-compatible endpoint:

```bash
LANGTEXTFLOW_OPENAI_COMPATIBLE_URL=http://127.0.0.1:1234/v1 \
LANGTEXTFLOW_OPENAI_COMPATIBLE_TRANSLATION_MODEL=my-model \
langtextflow-translation-benchmark benchmarks/translation.sample.jsonl \
  --provider openai-compatible \
  --repeats 5 \
  --output benchmark-results/translation-compatible.json
```

인증이 필요한 endpoint는 backend 환경변수 `LANGTEXTFLOW_OPENAI_COMPATIBLE_API_KEY`를 사용합니다. 결과 JSON에 API key는 기록하지 않습니다.

## 결과 지표

### Availability

- `prepare_ms`: provider readiness/model validation 시간
- `successful_runs`
- `failed_runs`
- `success_rate`

provider 초기화가 실패하면 report status는 `provider-unavailable`입니다. 일부 fixture만 실패하면 `partial-failure`입니다.

### Latency

성공한 호출만 대상으로 다음을 기록합니다.

- `latency_mean_ms`
- `latency_p50_ms`
- `latency_p95_ms`
- `latency_max_ms`

첫 호출에는 model warm-up 또는 KV/runtime 초기화 비용이 섞일 수 있습니다. 따라서 같은 fixture를 `--repeats 5` 이상 반복하고 p50과 p95를 함께 봅니다. cold-start 자체가 제품 요구사항이면 첫 호출 latency도 별도로 보존합니다.

### Reference quality

- `cer_mean`
- `wer_mean`
- `exact_match_rate`

CER/WER는 번역의 의미 동등성을 직접 측정하는 지표가 아닙니다. 표현이 다른 올바른 번역도 reference와 다르면 불리해집니다. 따라서 provider 순위를 CER/WER 하나로 결정하지 않습니다.

### Domain terminology

- `terminology_accuracy`
- `terminology_matched`
- `terminology_expected`
- fixture별 `missing`

교회/신학 용어, 성경명, 고유명사처럼 현장에서 치명적인 항목은 `expected_terms`로 별도 gate를 둡니다. LangTextFlow의 용도에서는 일반 문장 유사도보다 이 지표가 더 중요할 수 있습니다.

## 비교 원칙

두 provider/model을 비교할 때 다음을 고정합니다.

1. 같은 fixture 파일
2. 같은 `--repeats`
3. 같은 장비 또는 명시적으로 기록된 다른 장비
4. 같은 glossary/context
5. 가능한 한 같은 시점의 모델/runtime 버전
6. 다른 고부하 작업이 없는 환경

권장 결과표:

| Provider | Model | Success | p50 | p95 | CER | WER | Term accuracy |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ollama | model A | 100% | ... | ... | ... | ... | ... |
| OpenAI-compatible | model B | 100% | ... | ... | ... | ... | ... |

## 품질 gate

현재 단계에서는 임의의 통합 점수나 "translation confidence"를 만들지 않습니다. 대신 다음과 같은 다축 gate를 사용합니다.

- provider failure 0을 목표
- `terminology_accuracy`는 검수된 critical fixture에서 100%를 목표
- 숫자/장절/부정 표현 fixture는 사람이 별도 검수
- p95 latency가 실제 runtime의 postprocess budget을 초과하지 않을 것
- CER/WER는 동일 reference set에서 provider 간 상대 비교용으로 사용

실제 현장 데이터가 충분히 쌓인 뒤 semantic judge 또는 사람 평가와의 상관을 검증한 후에만 composite quality score나 calibrated confidence 도입을 검토합니다.

## 공개 저장소 주의

실제 설교, 간증, 발표 문장은 개인정보 또는 저작권 문제가 있을 수 있습니다. 공개 저장소에는 합성/익명화 fixture만 commit하고 실제 평가 corpus와 raw 결과는 별도 보관합니다.
