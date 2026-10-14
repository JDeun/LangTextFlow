# LangTextFlow 자율 모델 검증 컨베이어

SchemaRouter 0.14의 `research-0.14-conveyor.yml` 및
`research_014_conveyor.py` 구조에서 사용한 **사전 등록된 연구 DAG,
GitHub Actions 상태 감시, 정확한 소스 SHA 고정, 이벤트 기반 다음 단계
실행, 시간 간격 복구, 실패한 작업만 재시도, 결과 보존** 패턴을 차용했습니다.

## 사용자가 실행 버튼을 누르지 않아도 진행됩니다

이 컨베이어에는 `metadata` / `cpu` / `full` 수동 프로필이 없습니다.
`main`의 모델 평가 관련 구현 또는 연구 매니페스트가 변경되면
`experiment-conveyor.yml`의 `push` 이벤트가 자동으로 시작합니다.

이후 각 선행 모델 워크플로 종료 시 `workflow_run` 이벤트가 컨트롤러를
깨워 다음 실행을 발행하고, 매시간 17분(UTC) `schedule`이 누락된 완료
이벤트나 인프라 실패를 복구합니다. 매 시간 새 모델 실험을 시작하는
것이 아니라 **이미 발행된 고정 SHA의 미완료 연구**를 찾습니다.

컨트롤러와 각 과학 실험의 고정 입력은
`benchmarks/translation-conveyor-preregistration.json`에 기록됩니다.
각 독립 워크플로의 `run-name`과 `checkout.ref`에는 정확한 연구 시작
커밋 SHA를 전달합니다. 컨트롤러가 수정되더라도 진행 중인 실험의
코드나 평가 데이터를 다른 revision으로 교체하지 않습니다.

## 자동 실행 DAG

| 단계 | 실행 장치 | 선행 조건 | 실제 실행 |
| --- | --- | --- | --- |
| 1. Catalog | GitHub Actions Linux | 시작 SHA | 모델 후보 라이선스·검증 메타데이터 |
| 2. M2M100 | Linux CPU | Catalog 성공 | Qwen3-ASR + M2M100 실제 모델, 6방향 텍스트 + 3개 언어 FLEURS 음성 |
| 2b. Apple | macOS 26 러너 | Catalog 성공 | Swift Translation API 컴파일 + 6언어쌍 설치 상태 조회; **실제 번역 추론 아님** |
| 3. Church quality | Linux CPU | M2M100 성공 및 원본 아티팩트 존재 | TranslateGemma 4B 교회 텍스트 20개 품질 스모크 |
| 4. Live captions | Linux CPU | Church quality 성공 및 원본 아티팩트 존재 | 실제 읽기 음성을 ASR→MT→자막 경로로 재생 |

2와 2b는 서로 기다릴 필요가 없으므로 선행 Catalog 후 병렬로 진행될
수 있습니다. 두 작업이 실행 중이라고 해서 CI 체크 또는 품질 승인으로
판정하지 않습니다.

워크플로 실행이 성공하더라도 **원본 결과 아티팩트가 없거나 GitHub의
SHA-256 digest가 없다면** 다음 연구 단계로 넘어가지 않습니다.
부정적인 품질 결과가 들어 있는 정식 아티팩트가 존재하는 상태에서
실패했다면 이를 무조건 인프라 장애로 재시도하지 않습니다.
반대로 정식 결과가 없는 인프라 오류의 경우 같은 frozen 소스 SHA에서
`rerun-failed-jobs`를 최대 3회까지만 시도합니다.

실험 컨트롤러는 `concurrency`와 run-title identity를 이용해 동일
소스에 이미 실행 중인 워크플로를 중복 발행하지 않습니다. 처리 결과는
각 컨트롤러 GitHub Actions 실행의 `autonomous-translation-conveyor-state`
아티팩트와 Step Summary에 기록됩니다.

## 모든 후보가 자동 실험 가능한 것은 아닙니다

매니페스트는 **실제 실행 단계**와 **차단된 후보**를 반드시 나눕니다.

- NLLB-200 600M: CC BY-NC 4.0 및 연구용 조건. 상업적/일반 배포용
  자동 다운로드를 하지 않음
- Gemma 3 1B, TranslateGemma 4B: 모델 약관 및 gated access 승인과
  전용 평가 어댑터가 필요함
- Gemma 4 E2B, Qwen3 0.6B: 공개 후보는 등록했지만 해당 음성/텍스트
  평가 엔진 코드가 아직 없음
- Fun-ASR-MLT 핫워드: 실측 워크플로는 있으나 연구 PR #140이 현재
  미병합이므로 `main`의 자동 DAG에 포함되지 않음
- OPUS-MT ko→en: 연구 PR #119가 미병합이고 tc-big 조건이 실패했으므로
  제품 후보로 자동 통과시킬 수 없음
- Apple 시스템 번역: 호스팅 Mac에서 언어 리소스 가용성은 조회 가능하나
  Apple 시스템 음성 실시간 번역 모델과 실제 텍스트 번역 정확도는
  확인된 것이 아님

실행할 수 없는 후보를 **성공으로 처리하거나 무단으로 라이선스에
동의하지 않습니다.** 이 제한은 사용자가 매번 승인 버튼을 눌러야
한다는 뜻이 아니라, 권한이나 런타임 구현이 해결되기 전에는 해당
검증 자체가 차단됨을 의미합니다.

## 과학적 성능의 완료 조건은 별도입니다

GitHub Actions 초록색 체크는 코드 실행 성공일 뿐입니다.
최소 20개/방향의 사람 검수 설교 용어 데이터, 6방향 ASR hotword
ON/OFF × MT 사전 ON/OFF, 부정 표현 및 성경 장절 정확도, 지연 p95/p99,
30~90분 장시간 스트리밍, 실제 4GB/8GB 하드웨어 검증 없이는
`field_ready=false`를 유지합니다.

GitHub Actions Linux/macOS 테스트는 가능합니다. 다만 Mac 호스티드
러너의 결과를 사용자 Mac mini M4나 iPhone에 그대로 일반화하지
않습니다. Apple Translation 프레임워크는 OS에 설치된 언어 데이터에
의존하며, Apple의 비공개 FaceTime/AirPods 음성 통역 모델은
일반적인 외부 모델 API가 아닙니다.

## 관련 파일

- `.github/workflows/experiment-conveyor.yml`: 자동 시작, 이벤트 재개, 시간별 복구
- `scripts/autonomous_experiment_conveyor.py`: GitHub API 상태 확인·중복 방지·다음 단계 발행
- `benchmarks/translation-conveyor-preregistration.json`: 독립 연구 DAG, 차단 후보, 복구 정책
- `apps/server/tests/test_autonomous_experiment_conveyor.py`: 상태 전이, frozen SHA, 부정적 결과·재시도 방지 회귀
