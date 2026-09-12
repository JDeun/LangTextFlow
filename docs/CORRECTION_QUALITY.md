# Correction quality / calibration protocol

LangTextFlow의 correction layer는 STT 문장을 의미를 바꾸지 않는 범위에서 보정해야 합니다. 이 단계는 생성형 모델을 사용할 수 있으므로 단순 CER/WER만으로 합격시키지 않습니다. 특히 이미 올바른 문장을 잘못 바꾸는 regression과 숫자·장절·부정 표현 손상을 별도 위험으로 취급합니다.

## 1. 실행 경로

실제 제품과 같은 순서를 평가합니다.

```text
source transcript
  → DeterministicCorrector
  → optional constrained LLM corrector
      ├─ accepted result
      └─ timeout / unsafe / provider failure → deterministic fallback
```

CLI:

```bash
langtextflow-correction-benchmark benchmarks/correction.sample.jsonl \
  --provider none \
  --repeats 3 \
  --output benchmark-results/correction-deterministic.json
```

Ollama correction까지 포함:

```bash
langtextflow-correction-benchmark benchmarks/correction.sample.jsonl \
  --provider ollama \
  --model qwen3.5:4b \
  --repeats 3 \
  --output benchmark-results/correction-ollama.json
```

sample fixture는 harness smoke test용입니다. 실제 릴리스 판단에는 사람이 검수한 별도 corpus를 사용합니다.

## 2. Fixture schema

한 줄에 JSON object 하나를 저장합니다.

```json
{
  "id": "church-ko-john-316",
  "source_language": "ko",
  "source_text": "요한 보끔 3장 16절 말씀입니다",
  "expected_text": "요한복음 3장 16절 말씀입니다",
  "critical_tokens": ["요한복음", "3장", "16절"],
  "context": {
    "preset": "church",
    "reference_text": "오늘 본문은 요한복음 3장 16절입니다."
  }
}
```

`expected_text`는 사람이 직접 확인한 정답이어야 합니다. `critical_tokens`에는 의미 손실을 절대 허용하기 어려운 항목을 둡니다.

- 성경 책 이름 / 장절
- 사람·교회·지역 고유명사
- 숫자 / 날짜 / 인원
- 부정 표현
- 교리 핵심 용어

## 3. 측정 지표

### 정확도

- `exact_correct_rate`: 최종 결과가 human reference와 normalized exact match인 비율
- `cer_mean`, `wer_mean`: reference 대비 평균 문자/단어 오류율

한국어는 띄어쓰기 영향이 크므로 CER를 더 중요하게 해석합니다.

### Change detection

source와 reference가 다르면 `needs_change=true`입니다. correction 결과가 source와 달라졌으면 `predicted_change=true`입니다.

- precision: 모델이 바꾼 문장 중 실제 수정이 필요했던 비율
- recall: 실제 수정이 필요한 문장 중 모델이 수정한 비율
- F1: 위 두 값의 조화평균

### Regression safety

- `harmful_change_rate`: 이미 맞는 문장을 불필요하게 변경한 비율
- `missed_correction_rate`: 수정이 필요한 문장을 전혀 바꾸지 않은 비율
- `wrong_change_rate`: 수정은 했지만 human reference와 다른 결과가 된 비율

`harmful_change`는 일반적인 오탈자 누락보다 더 높은 위험으로 취급합니다. correction layer는 원문을 개선하는 기능이지 rewrite 기능이 아닙니다.

### Critical token safety

- `critical_token_accuracy`: fixture에 지정한 중요 문자열 보존율
- `numeric_token_accuracy`: reference의 숫자 token sequence와 결과의 숫자 token sequence 일치율

전체 CER가 낮아도 장절 `3:16 → 3:18`, 숫자 `15 → 50`, 부정 `아닙니다 → 입니다` 같은 오류는 릴리스 차단 사유가 될 수 있습니다.

### Runtime / degraded mode

- mean / p50 / p95 / max correction latency
- LLM attempted / applied count
- fallback count
- provider availability / provider error

provider failure 시 deterministic correction으로 내려가는 현재 제품 동작도 동일하게 측정합니다.

## 4. Quality gate

benchmark report와 별도 JSON policy를 사용합니다.

```bash
langtextflow-correction-gate \
  benchmark-results/correction-ollama.json \
  benchmarks/correction-policy.initial.json
```

통과 시 exit code `0`, 실패 시 `1`입니다. 존재하지 않거나 `null`인 metric은 통과로 간주하지 않습니다.

초기 policy는 다음 목적의 **provisional release-candidate gate**입니다. 최종 SLA가 아닙니다.

| Metric | Initial gate |
| --- | ---: |
| exact correct rate | >= 0.95 |
| change precision | >= 0.98 |
| change recall | >= 0.90 |
| harmful change rate | <= 0.01 |
| missed correction rate | <= 0.10 |
| wrong change rate | <= 0.03 |
| critical token accuracy | >= 0.99 |
| numeric token accuracy | 1.00 |
| correction latency p95 | <= 2500 ms |

이 숫자는 실제 교회/강연장 corpus가 확보되기 전의 초기 screening 기준입니다. baseline이 쌓이면 policy 파일만 변경하며 benchmark 코드에는 임계값을 하드코딩하지 않습니다.

## 5. Human-reviewed corpus 구성

confidence calibration과 릴리스 gate를 같은 데이터에서 동시에 맞추지 않습니다. 권장 구성은 session 단위 분리입니다.

```text
train / development: 60%
calibration:         20%
holdout test:        20%
```

같은 설교나 동일 화자의 연속 문장을 서로 다른 split에 섞지 않습니다. 문장 단위 random split은 context leakage를 만들 수 있습니다.

최소한 다음 strata를 포함합니다.

1. 이미 올바른 문장 — no-change regression 확인
2. deterministic alias로 해결되는 오류
3. context/glossary가 필요한 오류
4. LLM만으로 해결 가능한 발음·문맥 오류
5. 숫자 / 장절 / 날짜
6. 부정 표현
7. 고유명사
8. 한국어↔영어 code-switch
9. 잡음·잔향 환경의 실제 ASR 오류

실제 calibration을 시작할 때는 수십 개의 sample fixture가 아니라 최소 수백 개의 독립 human-reviewed segment를 확보하는 것을 권장합니다.

## 6. Confidence calibration 원칙

현재 LangTextFlow는 검증되지 않은 `0.87` 같은 per-segment confidence를 사용자에게 노출하지 않습니다. confidence는 모델의 자기평가 점수가 아니라 **실제 정답 확률과 대응하도록 calibration된 값**이어야 합니다.

향후 confidence를 도입할 때는 다음 순서를 따릅니다.

1. calibration split에서 correction 결과의 `exact_correct`를 binary target으로 사용합니다.
2. 사용 가능한 feature만 사용합니다. 예: deterministic/LLM method, normalized edit distance, critical-token preservation, provider가 제공하는 신뢰 가능한 score/logprob가 있을 경우 해당 값.
3. logistic calibration 또는 isotonic regression을 calibration split에 fit합니다.
4. holdout test에서 reliability curve, Brier score, ECE(Expected Calibration Error)를 측정합니다.
5. 같은 holdout으로 threshold를 반복 조정하지 않습니다.
6. 모델/provider/prompt가 변경되면 calibration artifact는 무효화하고 다시 fit합니다.

즉 현재 완료된 것은 **quality measurement + policy gate + calibration protocol**입니다. 실제 calibrated probability는 충분한 field corpus 없이 생성하지 않습니다.

## 7. 릴리스 판단 순서

1. deterministic-only baseline 실행
2. LLM-enabled candidate 실행
3. 동일 fixture SHA-256인지 확인
4. quality gate 실행
5. LLM이 deterministic baseline보다 `harmful_change`를 증가시키지 않는지 확인
6. critical/numeric regression을 fixture 단위로 검토
7. holdout corpus 결과가 통과하면 실제 30/60/90분 runtime soak test로 이동

benchmark report에는 환경 정보와 fixture SHA-256이 저장됩니다. 장비·model·fixture가 다른 결과를 동일 baseline처럼 직접 비교하지 않습니다.
