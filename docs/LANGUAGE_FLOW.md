# Language flow

LangTextFlow의 언어 흐름은 한국어 중심의 고정 방향이 아니라 **임의 source language → 하나 이상의 translation target** 구조입니다.

## 기본 규칙

1. `source_language`는 ASR이 인식하는 원문 언어입니다.
2. 원문 transcript는 항상 별도 track으로 보존되고 audience/display에서 선택할 수 있습니다.
3. `target_languages`는 번역할 언어 목록이며 source와 같을 수 없습니다.
4. target은 최소 1개가 필요하고 중복 값은 제거됩니다.
5. 하나의 corrected source segment에서 모든 target 번역을 생성해 같은 `translations` map에 저장합니다.

예:

```text
KO source
  ├─ original: KO
  ├─ translation: EN
  ├─ translation: JA
  └─ translation: ZH

EN source
  ├─ original: EN
  └─ translation: KO

JA source
  ├─ original: JA
  ├─ translation: KO
  └─ translation: EN
```

## Operator UX

입력 언어와 번역 언어는 독립적으로 선택합니다.

- source가 `KO`이면 초기 target은 `EN`입니다.
- source를 외국어로 바꿨는데 기존 target이 source와 충돌하거나 남는 target이 없으면 `KO`를 기본 target으로 선택합니다.
- source는 target selector에서 제외됩니다.
- 운영자 preview 언어는 원문 + 모든 번역 결과 중에서 별도로 선택합니다.
- 여러 target을 선택하면 번역 호출 수와 commit latency가 늘어날 수 있음을 UI에 표시합니다.

## Audience / Projector / OBS

청중에게 제공되는 언어 목록은 다음 합집합입니다.

```text
[source_language] + target_languages
```

원문을 선택하면 ASR/correction 결과를 표시하고, target 언어를 선택하면 해당 `translations[lang]`을 표시합니다. 번역 화면에서는 필요할 경우 원문을 보조 자막으로 함께 표시합니다.

## API invariant

서버는 UI와 별개로 다음 잘못된 요청을 거부합니다.

```json
{
  "source_language": "en",
  "target_languages": ["ko", "en"]
}
```

원문인 `en`은 번역 target이 될 수 없기 때문입니다.

유효한 요청 예시는 다음과 같습니다.

```json
{
  "source_language": "en",
  "target_languages": ["ko"]
}
```

```json
{
  "source_language": "ja",
  "target_languages": ["ko", "en"]
}
```

## 현재 처리 방식

현재 translation pipeline은 target language를 순차적으로 처리합니다. 이는 로컬 LLM에 동시에 여러 요청을 쏟아부어 GPU/RAM pressure를 높이는 것보다 예측 가능한 동작을 우선한 선택입니다.

실제 장비 benchmark에서 multi-target commit latency가 문제가 되는 경우 다음을 별도로 검토합니다.

- provider-level batching
- 한 번의 structured generation으로 여러 target 생성
- bounded translation worker pool
- target별 independent delivery / late patch

성능 최적화가 이루어져도 source/original track과 `translations` map 계약은 유지합니다.
