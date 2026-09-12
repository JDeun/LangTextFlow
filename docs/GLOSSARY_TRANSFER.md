# Glossary Import / Export

LangTextFlow 용어집은 JSON 또는 CSV로 내보내고 다시 가져올 수 있습니다.

## 기본 원칙

- export 파일에는 DB 내부 `id`, `created_at`, `updated_at`을 포함하지 않습니다.
- import는 파일 전체를 먼저 파싱하고 검증한 뒤 DB 변경을 시작합니다.
- 동일 용어 판단은 `term.casefold()` 기준입니다. 따라서 `OpenAI`와 `openai`는 충돌로 처리됩니다.
- 파일 내부에 같은 term이 두 번 있으면 import 자체를 거부합니다.
- `upsert` 정책은 기존 레코드의 `id`와 `created_at`을 유지하면서 내용을 갱신합니다.
- `skip` 정책은 기존 term과 충돌하는 항목을 변경하지 않습니다.
- import DB 반영은 하나의 SQLite transaction 안에서 수행됩니다.

## JSON

권장 형식은 다음과 같습니다.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "term": "칭의",
      "aliases": ["칭이"],
      "translations": {
        "en": "justification"
      },
      "category": "theology",
      "presets": ["church"],
      "boost": 1.5,
      "enabled": true
    }
  ]
}
```

최상위 객체 대신 entry 배열 자체도 import에서 허용합니다.

## CSV

CSV export 열은 다음과 같습니다.

```text
term,aliases_json,translations_json,category,presets_json,boost,enabled
```

`aliases_json`, `translations_json`, `presets_json`은 CSV 셀 내부에 JSON을 저장합니다. 이렇게 해야 쉼표가 포함된 별칭, 여러 언어 번역, 복수 preset을 손실 없이 보존할 수 있습니다.

예시:

```csv
term,aliases_json,translations_json,category,presets_json,boost,enabled
칭의,"[""칭이""]","{""en"": ""justification""}",theology,"[""church""]",1.5,true
```

CSV export는 Excel에서 한국어를 바로 열었을 때 인코딩 문제가 적도록 UTF-8 BOM을 포함합니다.

## API

### Export

```text
GET /api/v1/glossary/export?format=json
GET /api/v1/glossary/export?format=csv
```

### Import

```json
POST /api/v1/glossary/import
{
  "format": "json",
  "content": "...file contents...",
  "conflict_policy": "upsert"
}
```

응답은 다음 건수를 반환합니다.

```json
{
  "total": 10,
  "created": 6,
  "updated": 3,
  "skipped": 1
}
```

Operator API 정책과 동일하게 이 경로들은 local operator에서만 사용할 수 있습니다.
