# Local Model Setup Manager

LangTextFlow는 세션 시작 경로에서 큰 모델 다운로드가 발생하지 않도록, 모델 준비를 명시적인 operator setup 작업으로 분리합니다.

## 범위

현재 setup manager가 소유하는 작업은 다음 두 가지입니다.

1. 실행 중인 Ollama에 translation model pull 요청
2. 설치된 faster-whisper runtime이 사용하는 Hugging Face cache에 ASR model prefetch

운영체제 package, Python runtime, CUDA/driver, Ollama application 자체는 자동 설치하지 않습니다. 이러한 system-level dependency는 향후 desktop installer가 플랫폼별로 관리합니다.

## Job contract

모든 모델 준비 작업은 동일한 job state를 사용합니다.

```text
queued → running → completed
                 ↘ cancelled
                 ↘ error
```

job에는 provider, model, status, 진행 byte/percent, 오류, timestamps를 기록합니다. 같은 provider/model 조합이 이미 진행 중이면 새 다운로드를 만들지 않고 기존 active job을 반환합니다.

Operator API는 loopback-only입니다.

```text
GET  /api/v1/setup/jobs
GET  /api/v1/setup/jobs/{job_id}
POST /api/v1/setup/jobs/{job_id}/cancel
POST /api/v1/setup/ollama/pull
POST /api/v1/setup/faster-whisper/prefetch
```

## Ollama

Ollama pull은 local Ollama HTTP API의 streaming response를 backend가 소비합니다. status, digest, completed, total을 job state로 정규화하고 UI는 LangTextFlow job endpoint만 polling합니다.

취소는 LangTextFlow가 보유한 streaming request를 중단하는 best-effort 동작입니다. 별도의 Ollama server-side 강제 취소 API가 있다고 가정하지 않습니다.

모델 준비가 완료되면 preflight를 다시 실행해 translation model readiness를 확인합니다.

## faster-whisper

세션 실행과 사후 전사는 준비된 로컬 snapshot만 사용하며, 모델이나 토크나이저를 자동 다운로드하지 않습니다. 다운로드는 명시적 준비 작업의 전용 subprocess에서만 실행합니다. source 실행은 `python -m langtextflow.whisper_prefetch_worker`, frozen 실행은 전용 `--faster-whisper-prefetch-worker` 분기를 사용하며 일반 Python `-c` 코드를 실행하지 않습니다. 독립 ASR benchmark는 기존 bootstrap 동작을 보존하기 위해 다운로드 허용을 명시합니다.

관리 캐시는 `<model_cache_dir>/huggingface` 바로 아래의 기존 Hugging Face 구조를 유지합니다. 준비·preflight·라이브 ASR·사후 전사는 같은 조회 함수를 사용합니다. 명시한 로컬 모델 디렉터리가 우선이며, 관리 캐시의 직접 배치와 이전 `huggingface/hub` 배치, `HF_HUB_CACHE`/`HUGGINGFACE_HUB_CACHE`/`HF_HOME`, 기존 기본 캐시도 읽을 수 있습니다. 기존 모델을 이동·삭제하거나 강제로 다시 받지 않습니다.

`model.bin`, `config.json`, `tokenizer.json`이 모두 있어야 준비 완료로 판단합니다. `tokenizer.json`이 없을 때 native 라이브러리가 온라인 토크나이저로 대체하는 경로에 진입하지 않습니다. 공식 legacy 변환에는 `preprocessor_config.json`이 없는 경우가 있으므로, 이 파일은 선택적이며 없으면 native feature extractor의 로컬 기본값을 사용합니다. 이미 완성된 snapshot을 찾으면 다운로드와 다운로드 크기의 디스크 예약을 반복하지 않습니다.

캐시 비우기는 설정된 관리 영역만 지웁니다. 외부 HF 캐시나 명시적 로컬 모델 경로는 삭제하지 않으므로, 관리 캐시를 비워도 외부에 준비된 모델은 계속 사용할 수 있습니다.

subprocess를 사용한 이유는 다음과 같습니다.

- 다운로드 중 사용자 취소 시 실제 downloader process를 종료할 수 있음
- session runtime worker와 다운로드 lifecycle을 분리
- 완료된 파일은 faster-whisper가 원래 사용하는 Hugging Face cache에 남으므로 중복 저장 포맷이 없음

Preflight는 다음을 별개 항목으로 확인합니다.

```text
faster-whisper package
faster-whisper model cache
```

따라서 package만 설치되어 있고 model cache가 없는 상태를 production-ready fallback으로 오인하지 않습니다.

`Auto`에서 VibeVoice가 정상이라면 세션 자체는 시작할 수 있지만, faster-whisper cache가 없으면 완전한 failover readiness는 아닙니다. UI는 세션 전에 fallback model을 준비할 수 있는 repair action을 제공합니다.

## UI

모델이 없는 경우 다음 두 위치에서 repair action을 제공합니다.

- first-run onboarding의 System 단계
- operator System Preflight panel

작업 중에는 상태/진행률을 표시하고 취소할 수 있습니다. 완료 후 자동으로 preflight를 다시 실행합니다.

## 보안 / 안정성

- setup API는 operator loopback-only
- model 이름은 보수적인 문자 allowlist로 검증
- shell command를 조합하지 않고 subprocess argv를 사용
- 중복 active download를 방지
- app shutdown 시 active job을 취소
- system package/driver 변경 없음

## 다음 단계

- VibeVoice sidecar lifecycle manager
- packaged desktop runtime에서 provider dependency install/repair
- model cache disk usage / cleanup UI
- download retry/backoff와 diagnostics bundle 연동
