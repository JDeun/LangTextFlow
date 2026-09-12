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

## WebSocket 동작

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
