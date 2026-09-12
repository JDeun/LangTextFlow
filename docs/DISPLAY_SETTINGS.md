# Caption display profile

LangTextFlow의 자막 표시 설정은 브라우저 한 화면의 임시 CSS가 아니라 **세션 context에 포함되는 Display Profile**입니다. 운영자가 세션 시작 전에 정한 profile을 audience, projector, OBS Browser Source가 동일하게 사용합니다.

## 설정 항목

| 항목 | 범위 | 기본값 |
| --- | --- | --- |
| 글꼴 | system / sans / serif / mono | system |
| 글자 크기 | 70–180% | 100% |
| 최대 행수 | 1–4줄 | 2줄 |
| 유지시간 | 0–30초 | 8초 |
| 번역 시 원문 병기 | on/off | on |
| 정렬 | left / center | center |

`0초` 유지시간은 다음 자막이 올 때까지 현재 자막을 계속 표시한다는 뜻입니다.

## 수명주기

```text
Operator local preference
        ↓
Session start snapshot
        ↓
SessionContext.display_settings
        ↓
AudienceSessionView
        ↓
Audience / Projector / OBS
```

운영자 설정은 브라우저 localStorage에 저장되어 다음 실행의 기본값으로 사용됩니다. 세션이 시작되면 해당 값을 context에 snapshot하고, 세션 중에는 편집을 잠급니다. 이 방식은 이미 열린 projector/OBS 화면과 operator 설정이 서로 달라지는 상황을 피하기 위한 것입니다.

## Live caption hold policy

유지시간은 **committed segment**에만 적용합니다.

- partial / stable / corrected / translated 단계: 다음 revision 또는 commit을 기다리는 동안 표시 유지
- committed 단계: `hold_seconds` 이후 live caption 영역에서 숨김
- `hold_seconds = 0`: 다음 segment가 도착할 때까지 유지
- transcript/history에는 유지시간을 적용하지 않음

즉 live 화면에서 자막이 사라져도 세션 transcript와 SQLite 기록은 그대로 보존됩니다.

## 원문 병기

표시 언어가 source language이면 원문만 표시합니다.

표시 언어가 translation target이고 실제 번역 결과가 존재할 경우 `show_source_when_translated=true`이면 다음처럼 렌더링합니다.

```text
[번역 자막]
[원문 보조 자막]
```

이 옵션은 audience, projector, OBS, operator preview에 동일한 의미를 가집니다.

## 렌더러

`LiveCaption` 컴포넌트가 다음 surface를 공유합니다.

- `audience`
- `preview`
- `display` (projector / OBS)

surface별 기본 시각 크기는 다르지만 Display Profile의 scale, font, line clamp, alignment, source toggle, hold policy는 동일하게 적용됩니다.
