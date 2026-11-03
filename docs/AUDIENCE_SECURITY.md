# Audience Join-Code Security

LangTextFlow의 audience/projector/OBS 경로는 LAN 또는 Tailscale 네트워크의 다른 장치에서 접근할 수 있습니다. Operator API는 loopback-only이지만 audience 경로는 join code를 인증 수단으로 사용하므로 무차별 대입 방어가 필요합니다.

## 기본 정책

현재 join code는 사람이 직접 입력하기 쉬운 6자리 문자열을 유지합니다. 사용 alphabet은 혼동하기 쉬운 문자를 제외한 32개 문자이며 코드 공간은 `32^6 = 1,073,741,824`개입니다.

잘못된 join code 시도만 client IP별로 기록합니다.

기본값:

```text
실패 허용 횟수: 8회
관찰 window: 60초
차단 시간: 300초
최대 추적 client: 4096개
```

8번째 실패부터 HTTP는 `429 Too Many Requests`와 `Retry-After`를 반환합니다. Audience WebSocket은 application close code `4429`로 종료합니다.

정상 join code 인증에 성공하면 해당 IP의 이전 실패 기록을 즉시 삭제합니다. 따라서 정상적인 caption REST 조회나 장시간 WebSocket 연결은 rate limiting 대상이 아닙니다.

## Client identity

Rate limiter는 실제 socket connection의 client host를 사용합니다. `X-Forwarded-For` 같은 request header를 신뢰하지 않습니다. 직접 LAN/Tailscale에서 실행하는 현재 배포 모델에서 사용자가 임의 header로 client identity를 우회하는 것을 막기 위한 정책입니다.

향후 trusted reverse proxy를 공식 지원할 경우에는 별도의 proxy trust 설정과 allowlist를 도입한 뒤 forwarded address를 해석해야 합니다.

## REST 동작

보호 대상:

```text
GET /api/v1/audience/{join_code}
GET /api/v1/audience/{join_code}/captions
```

잘못된 code가 threshold 미만이면 기존과 동일하게 `404`를 반환합니다. Threshold에 도달하거나 차단 기간 중이면 `429`를 반환합니다.

## 선택적 화면 지연 보고

`POST /api/v1/audience/{join_code}/metrics/render`만 별도의 수집 경로로 허용합니다. 운영자가 세션 시작 전에 수집을 켜고 청중이 자신의 화면에서 동의해야 브라우저가 보고를 보냅니다. 두 선택은 기본 비활성화이며 청중 동의는 저장하지 않습니다. 프로젝터/OBS는 보고하지 않습니다.

서버는 활성 join code, 동의 헤더, Origin/Fetch-Metadata, JSON 형식, 최대 512바이트 및 2초 본문 수신 제한을 검사합니다. 허용 필드는 stage, 0~60,000ms의 유한한 표시 지연, `double_raf_estimate` 방식뿐입니다. 자막·기기 ID·클라이언트 시각 필드는 거부합니다. 실제 연결 주소는 최대 1,024개 항목의 임시 rate-limit 키로만 사용하며 집계에 넣지 않습니다(연결별 60초에 최대 12회). 기존 서버 접속 로그에는 연결 메타데이터가 기록될 수 있습니다.

본문 수신 중 세션이 바뀌면 409로 거부합니다. 시작/종료 시 집계와 rate limiter를 초기화합니다. 최근 60초·단계별 최대 512개 표본의 p50/p95/p99와 개수만 운영자에게 제공합니다. 이 경로는 자막이나 다른 운영자 기능을 변경할 수 없습니다.

## WebSocket 인증

보호 대상:

```text
WS /ws/audience/{join_code}
```

- 잘못된 code, threshold 미만: `4404`
- 차단 상태 또는 threshold 도달: `4429`
- 정상 code: 실패 state 초기화 후 caption socket 연결

## 설정

모든 값은 `LANGTEXTFLOW_` prefix 환경변수로 변경할 수 있습니다.

```text
LANGTEXTFLOW_AUDIENCE_JOIN_MAX_FAILURES=8
LANGTEXTFLOW_AUDIENCE_JOIN_WINDOW_SECONDS=60
LANGTEXTFLOW_AUDIENCE_JOIN_BLOCK_SECONDS=300
LANGTEXTFLOW_AUDIENCE_JOIN_MAX_TRACKED_CLIENTS=4096
```

## 메모리 제한

Limiter state는 프로세스 메모리에만 보관하며 영속화하지 않습니다. 추적 client 수가 설정값을 넘으면 가장 오래 사용되지 않은 state부터 제거합니다. 따라서 임의 source address가 매우 많이 들어와도 limiter state 자체가 무한히 증가하지 않습니다.

현재 desktop/local-first 배포는 단일 backend process를 전제로 합니다. 향후 여러 worker 또는 여러 서버 instance로 확장하면 Redis 같은 공유 rate-limit store가 필요합니다.
