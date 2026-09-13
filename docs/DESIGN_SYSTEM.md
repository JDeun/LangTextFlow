# LangTextFlow UI/UX Design System

이 문서는 LangTextFlow의 인터페이스를 변경할 때 유지해야 하는 제품 디자인 계약을 정의합니다. 목표는 특정 SaaS의 외형을 복제하는 것이 아니라, **일반 사용자가 쉽게 시작할 수 있는 clean communication SaaS shell**과 **실시간 운영에 필요한 professional operator layer**를 결합하는 것입니다.

## 1. Product design principles

1. **Caption first** — 실시간 자막이 항상 가장 중요한 콘텐츠입니다.
2. **Progressive disclosure** — 일반 사용자가 세션을 시작하는 데 필요하지 않은 엔진/모델/telemetry 설정은 기본적으로 접습니다.
3. **Operational visibility** — LIVE/STARTING/FAILED, microphone, ASR, translation, persistence, audience connection처럼 운영 판단에 필요한 상태는 숨기지 않습니다.
4. **Failure is a first-class state** — loading/empty/degraded/error/reconnecting 상태를 정상 상태와 동일한 수준으로 설계합니다.
5. **Audience is not Operator** — LAN audience에는 읽기 전용 자막 소비 기능만 제공합니다. operator control을 시각적으로나 API적으로 노출하지 않습니다.
6. **International by default** — UI copy는 en/ko/ja locale catalog를 통해 제공하며 raw CJK UI literal을 컴포넌트에 직접 추가하지 않습니다.
7. **Accessible before decorative** — 색상만으로 상태를 표현하지 않고, keyboard focus, reduced motion, 충분한 contrast와 readable type scale을 우선합니다.

## 2. Surface model

LangTextFlow는 하나의 시각 밀도를 모든 화면에 강제하지 않습니다.

### Operator

- 밝은 neutral surface를 기본 shell로 사용합니다.
- source/target language, microphone, session start/stop 등 핵심 조작은 즉시 보입니다.
- context/glossary, ASR/model, diagnostics 등은 progressive disclosure를 사용합니다.
- live preview는 주변 control surface와 구분되는 dark stage를 허용합니다.
- 상태 badge는 짧고 일관된 vocabulary를 사용합니다.

### Audience

- 자막 소비가 목적입니다.
- operator 설정, telemetry, provider 선택 등 운영 제어를 표시하지 않습니다.
- 언어 선택과 연결 상태 외에는 가능한 한 인터페이스 chrome을 줄입니다.
- 모바일 viewport에서 horizontal overflow가 없어야 합니다.

### Projector / OBS

- 장시간 읽기와 영상 합성을 우선합니다.
- dark stage 또는 transparent surface를 사용합니다.
- display profile(font, size, max lines, alignment, source 병기)을 공통 계약으로 사용합니다.
- 설정 UI를 출력 화면에 노출하지 않습니다.

### Onboarding / Preflight

- 첫 사용자는 내부 provider 구조를 이해하지 않아도 세션 준비 여부를 판단할 수 있어야 합니다.
- 단계는 system → microphone → language/use case → readiness 순으로 설명합니다.
- 문제는 단순한 실패 표시보다 가능한 repair action을 함께 제공합니다.
- 모바일에서는 preflight가 핵심 session controls를 밀어내지 않도록 기본 밀도를 낮춥니다.

## 3. Visual language

현재 제품 방향은 **clean communication SaaS + operational layer**입니다.

- neutral/light application shell
- restrained green accent for healthy/ready/live semantics
- subtle border and elevation hierarchy
- moderate corner radius; 장식적 pill 남용 금지
- 충분한 whitespace를 사용하되 운영 정보의 scanability를 희생하지 않음
- dark surfaces는 live caption stage, projector/OBS처럼 콘텐츠 집중 목적일 때 사용
- 강한 dark block을 일반 settings/history selection에 무분별하게 사용하지 않음

특정 경쟁 제품의 색상, 레이아웃, 아이콘을 그대로 복제하지 않습니다. 참고 제품에서 가져오는 것은 progressive disclosure, audience simplicity, operator status visibility 같은 **interaction pattern**입니다.

## 4. Information hierarchy

Operator의 기본 우선순위는 다음과 같습니다.

1. 현재 session / LIVE 상태
2. microphone 및 audio health
3. source / target language
4. live caption preview
5. start / stop action
6. blocking preflight 및 recoverable error
7. audience sharing
8. context / glossary
9. engine / model / telemetry / diagnostics
10. history / export

고급 기능을 추가할 때 1–6번보다 시각적으로 강하게 만들지 않습니다.

## 5. Interaction states

모든 network/provider 기반 component는 최소 다음 상태를 고려합니다.

- idle
- loading / starting
- ready
- live
- degraded
- reconnecting
- failed
- empty
- disabled

비동기 요청은 stale response가 최신 사용자 선택을 덮어쓰지 않도록 ordering/cancellation을 고려해야 합니다. 버튼에서 시작한 Promise rejection은 UI error state로 수렴해야 하며 unhandled rejection으로 남기지 않습니다.

## 6. Responsive contract

- Operator desktop은 control surface + caption stage의 scanability를 우선합니다.
- 좁은 viewport에서는 secondary panels를 축소하고 핵심 session controls를 먼저 노출합니다.
- Audience는 phone portrait를 1급 viewport로 취급합니다.
- 어떤 주요 화면도 정상 UI 상태에서 document-level horizontal scrolling을 요구해서는 안 됩니다.
- sticky action은 뒤의 설정 항목을 가리지 않도록 safe spacing을 확보합니다.

## 7. Localization contract

Built-in interface locales는 현재 다음 세 가지입니다.

- English (`en`) — canonical fallback
- 한국어 (`ko`)
- 日本語 (`ja`)

사용자 선택 → persisted locale → browser locale → English fallback 순으로 결정합니다.

새 UI 문자열은 locale catalog에 추가합니다. 컴포넌트에 번역 대상 raw CJK literal을 직접 추가하지 않습니다. 서버 오류는 가능한 경우 stable error code를 전달하고 UI가 locale에 맞게 설명하도록 설계합니다.

향후 SQLite는 built-in UI catalog의 필수 source of truth가 아니라 optional translation override / external language-pack layer로 확장할 수 있습니다. 기본 UI는 backend/DB 장애가 있어도 렌더 가능해야 합니다.

## 8. Accessibility contract

- keyboard-only navigation이 가능해야 합니다.
- `:focus-visible`을 제거하지 않습니다.
- status는 색상 외 text/icon semantics를 함께 사용합니다.
- `prefers-reduced-motion`을 존중합니다.
- caption text는 장시간 읽기에 적합한 contrast와 line length를 유지합니다.
- touch target은 모바일에서 충분한 크기를 확보합니다.
- accessibility acceptance가 완료되기 전에는 roadmap의 해당 항목을 완료 처리하지 않습니다.

## 9. CSS / component hygiene

- 디자인 변경을 override layer의 무한 누적으로 해결하지 않습니다.
- 반복되는 color/spacing/radius/elevation은 token으로 수렴시킵니다.
- literal `\\n`/`\\r`, brace imbalance, `javascript:`/`expression()` 등 비정상 CSS는 CI hygiene gate가 차단해야 합니다.
- component state와 visual state의 의미를 일치시킵니다.
- temporary audit/patch workflow와 generated artifact를 제품 tree에 남기지 않습니다.

## 10. Regression gates

UI 변경 PR은 최소 다음을 유지해야 합니다.

- frontend policy/i18n tests
- TypeScript type-check
- production build
- CSS hygiene gate
- Browser E2E
- en → ko → ja locale switching
- Operator session start/live flow
- Audience entry
- advanced settings default disclosure state
- mobile horizontal-overflow assertion
- Audience에 operator control이 노출되지 않는지 확인
- Windows/macOS boundary smoke

시각적으로 큰 변경은 desktop Operator, mobile Operator, Audience, Onboarding 상태를 screenshot으로 확인합니다.

## 11. Change decision rule

새 디자인 아이디어는 다음 순서로 판단합니다.

1. 사용자가 더 빨리 이해하거나 조작할 수 있는가?
2. live operation에서 중요한 상태를 숨기지 않는가?
3. Audience의 자막 가독성을 높이는가?
4. mobile / keyboard / localization에서 깨지지 않는가?
5. 기존 기능과 security boundary를 보존하는가?
6. 단순한 장식 변화보다 유지보수 가능한 design-system 개선인가?

이 조건을 충족하지 못하는 시각 변경은 제품 일관성을 이유로 추가하지 않습니다.
