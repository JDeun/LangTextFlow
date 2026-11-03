# Glossary Import / Export

LangTextFlow 용어집은 JSON 또는 CSV로 내보내고 다시 가져올 수 있습니다.

## 기본 원칙

- export 파일에는 DB 내부 `id`, `created_at`, `updated_at`을 포함하지 않습니다.
- import는 파일 전체를 먼저 파싱하고 검증한 뒤 DB 변경을 시작합니다.
- 전역 용어집의 import identity는 `(source_language, term.casefold(), sorted(presets))`입니다. 대소문자와 프리셋 목록 순서가 달라도 같은 identity로 처리됩니다.
- 빈 `presets`는 전역값입니다. 같은 입력 언어·용어에 전역값과 프리셋값, 서로 겹치지 않는 프리셋값을 함께 저장할 수 있습니다. 전역값끼리 또는 교집합이 있는 프리셋값끼리는 충돌합니다.
- 파일 내부의 중복 identity나 서로 겹치는 프리셋 범위는 거부합니다. DB의 다른 프리셋 범위와 겹치는 경우에도 전체 import를 취소하며 기존 레코드를 변경하지 않습니다.
- `upsert` 정책은 기존 레코드의 `id`와 `created_at`을 유지하면서 내용을 갱신합니다.
- `skip` 정책은 같은 identity의 기존 항목을 변경하지 않습니다. 같은 용어라도 별도 전역/프리셋 identity는 새 항목으로 처리합니다.
- import DB 반영은 하나의 SQLite transaction 안에서 수행됩니다.
- 세션에는 전역값을 먼저 적용한 뒤 선택한 프리셋값을 덮어씁니다. 프리셋값이 비활성화되어 있으면 해당 전역값도 제외합니다. 교회 seed는 어느 범위에든 같은 입력 언어·용어가 있으면 기존 운영자 항목을 보존합니다.
- 프로젝트 용어집은 기존의 `(source_language, term.casefold())` 단일 identity를 유지합니다. 같은 용어의 여러 범위를 한 파일로 프로젝트에 가져오면 거부하며, 전역 용어집의 레이어를 조용히 합치지 않습니다.

## JSON

권장 형식은 다음과 같습니다.

```json
{
  "schema_version": 1,
  "entries": [
    {
      "term": "칭의",
      "source_language": "ko",
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
term,source_language,aliases_json,translations_json,category,presets_json,boost,enabled
```

`aliases_json`, `translations_json`, `presets_json`은 CSV 셀 내부에 JSON을 저장합니다. 이렇게 해야 쉼표가 포함된 별칭, 여러 언어 번역, 복수 preset을 손실 없이 보존할 수 있습니다.

예시:

```csv
term,source_language,aliases_json,translations_json,category,presets_json,boost,enabled
칭의,ko,"[""칭이""]","{""en"": ""justification""}",theology,"[""church""]",1.5,true
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
