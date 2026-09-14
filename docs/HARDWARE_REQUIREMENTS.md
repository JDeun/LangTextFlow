# Hardware Requirements

LangTextFlow는 사용할 모델과 provider 조합에 따라 필요한 하드웨어가 크게 달라집니다. 따라서 하나의 숫자를 "최소 사양"으로 제시하지 않고, **실행 가능한 최소 구성**, **현재 기본 로컬 스택 권장 구성**, **장시간 행사 운영 권장 구성**을 구분합니다.

> [!IMPORTANT]
> 이 문서는 **pre-field hardware guidance**입니다. 자동 테스트와 모델 파일 크기, 런타임 구조를 기준으로 보수적으로 작성했으며, 특정 CPU/GPU SKU에 대한 production certification은 아닙니다. 실제 target hardware에서 `BENCHMARK.md`와 `RELEASE_CHECKLIST.md`의 30/60/90분 acceptance를 수행한 뒤 수치를 조정합니다.

## 빠른 결론

| 프로필 | CPU | 메모리 | GPU / 가속기 | 저장공간 여유 | 적합한 구성 |
|---|---|---:|---|---:|---|
| **최소 / 경량** | 최근 4코어 이상 x86-64 또는 Apple Silicon | **16 GB RAM** | 필수 아님 | **30 GB+** | faster-whisper `tiny/base/small` + 외부/OpenAI-compatible 번역 또는 correction/translation 비활성화 |
| **권장 / 기본 로컬** | 최근 6~8코어 이상 | **32 GB RAM 이상** | **NVIDIA 24 GB VRAM 권장** 또는 **Apple Silicon 48 GB+ unified memory 권장** | **50 GB+** | VibeVoice 7B + Qwen 3.5 4B correction + TranslateGemma 4B translation |
| **행사 운영 / 여유형** | 최근 8코어 이상 고성능 CPU | **64 GB RAM** | **24 GB+ VRAM** 또는 **64 GB+ unified memory** | **80 GB+** | all-local, 복수 target language, 장시간 세션, 모델 캐시/업데이트 여유 포함 |

위 표의 `최소`는 **프로그램이 의미 있게 동작할 수 있는 최소 프로필**이지, 현재 기본 VibeVoice 로컬 스택을 모두 동시에 빠르게 구동하기 위한 최소치가 아닙니다.

## 왜 기본 로컬 스택은 사양이 높은가

현재 기본 모델의 배포 크기는 대략 다음과 같습니다.

| 역할 | 기본 모델 | 모델 파일 규모 | 비고 |
|---|---|---:|---|
| Streaming ASR | `microsoft/VibeVoice-ASR-Streaming-7B` | 약 **17.4 GB** | primary streaming ASR |
| Correction | `qwen3.5:4b` | 약 **3.4 GB** | Ollama 기본 correction |
| Translation | `translategemma:4b` | 약 **3.3 GB** | Ollama 기본 translation |

모델 파일만 합쳐도 약 **24 GB**입니다. 실제 실행 중에는 model weights 외에도 KV/cache, framework/runtime, audio buffer, Python backend, Ollama, Tauri/WebView, SQLite, 임시 다운로드 파일이 메모리와 디스크를 추가로 사용합니다.

따라서 "모델 파일 합계와 동일한 RAM/VRAM"을 최소 사양으로 잡으면 안정적인 장시간 realtime 동작에 필요한 headroom이 없습니다.

## 1. 최소 / 경량 프로필

### 권장 기준

- CPU: 최근 세대 4코어 이상
- RAM: **16 GB**
- GPU: 없어도 가능
- SSD: **30 GB 이상 여유 공간**
- 네트워크: 외부 translation provider를 사용할 경우 안정적인 인터넷 연결

### 권장 모델 구성

```text
ASR          faster-whisper tiny/base/small
Correction   off 또는 작은 Ollama 모델
Translation  OpenAI-compatible external provider 또는 필요 시 off
```

이 프로필은 VibeVoice 7B를 기본 전제로 하지 않습니다. GPU가 없는 일반 노트북이나 16 GB Apple Silicon 장비에서 먼저 기능을 확인하려면 faster-whisper를 primary로 사용하는 편이 현실적입니다.

### 기대 수준

- 소규모 테스트와 개발
- 단일 target language
- 짧은 세션
- 외부 translation provider 사용

CPU-only ASR은 장비에 따라 realtime factor가 크게 달라질 수 있으므로 **16 GB RAM이라는 이유만으로 realtime 성능을 보장하지 않습니다.**

## 2. 권장 / 기본 로컬 프로필

현재 LangTextFlow 기본 구성인 VibeVoice + local correction + local translation을 목표로 합니다.

### Windows / Linux 계열 GPU 장비

- CPU: 최근 세대 6~8코어 이상
- RAM: **32 GB 이상**
- GPU: **NVIDIA GPU, 24 GB VRAM 권장**
- SSD: NVMe 권장, **50 GB 이상 여유 공간**

VibeVoice 원본 weight가 약 17.4 GB이므로 16 GB VRAM은 runtime overhead와 다른 로컬 모델을 고려하면 보수적인 권장치로 보기 어렵습니다. 일부 offload/quantization 조합에서는 더 낮은 VRAM에서도 동작할 수 있지만, 현재 LangTextFlow의 production baseline으로 보장하지 않습니다.

### Apple Silicon

- Apple Silicon
- **48 GB unified memory 이상 권장**
- **64 GB unified memory이면 장시간 all-local 운영에 더 적합**
- SSD **50 GB 이상 여유 공간**

Apple Silicon은 CPU와 GPU가 unified memory를 공유하므로 NVIDIA의 `VRAM + system RAM`과 직접 1:1 비교하면 안 됩니다. 32 GB 모델도 구성에 따라 실행 가능할 수 있지만 VibeVoice, Ollama 모델, WebView/backend를 함께 운용할 때 memory pressure가 커질 수 있어 현재는 **48 GB를 권장선**으로 둡니다.

## 3. 장시간 행사 운영 프로필

다음 조건을 목표로 하는 프로필입니다.

- 30/60/90분 이상 연속 세션
- correction + translation 모두 local
- 복수 target language fan-out
- Audience/Projector/OBS 동시 사용
- runtime/model cache와 updater를 위한 충분한 headroom

권장 기준:

- CPU: 최근 고성능 8코어 이상
- RAM: **64 GB**
- NVIDIA: **24 GB VRAM 이상**
- Apple Silicon: **64 GB unified memory 이상**
- SSD: **80 GB 이상 여유**, NVMe 권장
- 전원: 노트북은 AC 전원 연결 및 절전/배터리 성능 제한 해제
- 네트워크: Audience 사용 시 안정적인 5 GHz/6 GHz Wi-Fi 또는 유선 LAN backbone 권장

여기서 64 GB는 모델이 반드시 64 GB를 소비한다는 뜻이 아니라, realtime 행사 중 OS memory pressure, model reload, cache, export, 브라우저 surface를 포함한 **운영 여유를 확보하기 위한 권장치**입니다.

## GPU가 꼭 필요한가요?

아닙니다. LangTextFlow는 faster-whisper와 외부 translation provider를 조합하면 GPU 없이도 사용할 수 있습니다.

다만 다음 조건에서는 GPU/Apple Silicon 가속을 강하게 권장합니다.

- VibeVoice를 primary ASR로 사용
- correction과 translation을 모두 local로 사용
- 여러 target language를 동시에 번역
- 낮은 end-to-end latency가 중요한 행사

CPU-only 환경에서는 모델 선택을 줄이거나 외부 provider를 사용하는 것이 더 안정적일 수 있습니다.

## 저장공간 계산

현재 기본 모델 파일은 약 24 GB이므로 설치 시에는 최소한 다음을 고려합니다.

```text
VibeVoice                    ~17.4 GB
Qwen 3.5 4B correction       ~ 3.4 GB
TranslateGemma 4B            ~ 3.3 GB
--------------------------------------
model files                  ~24.1 GB
+ Python/runtime/Ollama/cache
+ temporary download/update space
+ session DB/export files
```

따라서:

- **30 GB free:** 경량 구성의 하한선
- **50 GB free:** 기본 로컬 구성을 위한 권장선
- **80 GB+ free:** 장시간 운영과 여러 모델 실험에 권장

LangTextFlow는 큰 모델 provisioning 전에 free-space guard를 수행하지만, 운영자가 OS 전체 디스크를 거의 가득 채운 상태로 사용하는 것을 권장하지 않습니다.

## 네트워크 요구사항

### 완전 로컬 구성

ASR/correction/translation이 모두 local이고 필요한 모델이 이미 설치되어 있다면 세션 자체는 외부 인터넷 없이 운영할 수 있습니다.

### 인터넷이 필요한 경우

- 최초 모델/runtime 다운로드
- OpenAI-compatible remote provider
- desktop update 확인/다운로드

### Audience LAN

Audience/Projector/OBS 공유에서 중요한 것은 인터넷 회선 속도보다 **Operator PC와 청중 단말 사이의 로컬 네트워크 품질**입니다.

행사장에서는 가능하면:

- Operator PC는 유선 Ethernet
- Audience는 전용 또는 혼잡도가 낮은 AP
- AP client isolation 여부 사전 확인
- VPN/guest network가 local peer access를 차단하는지 확인

을 권장합니다.

## 마이크 / 오디오 인터페이스

최소 입력은 OS와 브라우저/WebView에서 정상적으로 인식되는 microphone device입니다.

권장:

- USB audio interface 또는 안정적인 USB microphone
- 행사장 mixer에서 별도 AUX/monitor output을 받아 line input으로 입력
- OS echo cancellation/automatic gain control이 실제 음원에 악영향을 주는지 사전 확인

노트북 내장 마이크도 동작할 수 있지만 강당, 교회, 컨퍼런스처럼 거리와 반향이 큰 환경에서는 **컴퓨팅 사양보다 clean audio feed가 ASR 정확도에 더 큰 영향을 줄 수 있습니다.**

## 구성별 예시

### A. 일반 노트북 / 개발 테스트

```text
16 GB RAM
GPU 없음 또는 내장 GPU
faster-whisper small
translation = external provider
```

**판정:** 기능 검증 가능. 기본 all-local VibeVoice 스택 권장 대상은 아님.

### B. 고성능 Windows workstation

```text
32~64 GB RAM
NVIDIA 24 GB VRAM
NVMe SSD
VibeVoice + Qwen 4B + TranslateGemma 4B
```

**판정:** 현재 기본 local stack의 우선 benchmark 대상.

### C. Apple Silicon workstation

```text
48~64 GB unified memory
Apple Silicon
NVMe-class internal SSD
VibeVoice + Ollama local stack
```

**판정:** 기본 local stack의 우선 benchmark 대상. 실제 모델별 Metal/CPU offload 특성은 field benchmark로 확정.

## 사양을 문서에서 확정하는 방법

향후 실제 장비 테스트 결과가 쌓이면 이 문서는 단순 권장치에서 **검증된 hardware matrix**로 발전시킵니다.

각 장비에서 최소한 다음을 기록합니다.

| 항목 | 기록값 |
|---|---|
| CPU / SoC | 모델명 |
| RAM / unified memory | GB |
| GPU | 모델명 / VRAM |
| OS | 버전 |
| ASR engine/model | VibeVoice 또는 faster-whisper 모델 |
| correction model | 모델명 |
| translation model/provider | 모델명/provider |
| target language 수 | N |
| 세션 길이 | 30/60/90분 |
| p50/p95 caption latency | ms |
| peak RAM/VRAM | GB |
| dropped/failed events | count |
| 최종 판정 | supported / constrained / unsupported |

검증된 장비가 충분히 쌓이기 전에는 특정 GPU/맥 모델을 "공식 지원"이라고 표현하지 않습니다.

## 관련 문서

- [Model Guide](MODEL_GUIDE.md)
- [Model Setup](MODEL_SETUP.md)
- [Preflight](PREFLIGHT.md)
- [Benchmark](BENCHMARK.md)
- [Release Checklist](RELEASE_CHECKLIST.md)
- [Known Limitations](KNOWN_LIMITATIONS.md)
