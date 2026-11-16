# Cicada 선택 이식: Ming 로컬 실험과 Mac의 Apple 우선 정책

- **Status:** investigating; 로컬 TTS smoke와 환경 사전점검. 제품 통합·기본값 승격·현장 승인 아님.
- **Date / researcher:** 2026-11-16 / repository agent
- **Tracking issue:** [#468](https://github.com/JDeun/LangTextFlow/issues/468)
- **Scope:** ASR, MT, TTS, platform, privacy
- **Last reviewed:** 2026-11-16

## 사용자 결정과 이번 변경의 범위

2026-11-16 후속 대화에서 사용자는 유료 API를 사용할 수 없다는 조건과
회의 데이터의 외부 전송 우려를 제시했고, 공개된
`Ming-3.0-audio-flash`와 `Ming-3.0-tts-0.5B`의 로컬 테스트 및 Mac에서
Apple 로컬 모델을 사용하는 방향을 요청했다. 이 결정은 #468의 선택
이식을 구체화한다. Cicada의 Ling/Ming API·Google fallback을 필수
의존성으로 가져오는 방향으로 해석하지 않는다.

이번 PR은 독립적인 수동 실험과 기록이다. runtime/UI 기본값, Rust
authority 전환, 자동 실험 workflow, 기존 pause marker는 변경하지 않는다.
Apple ASR helper 구현·Mac 실행·제품의 자동 선택은 후속 구현 항목이다.
현재 결과를 이유로 기존 [품질 기준](../QUALITY_ACCEPTANCE_PLAN.md)을
충족했다고 판단하지 않는다.

## 가설과 판단할 선택

1. 지원 Mac에서 Apple 기기 내 ASR를 기존 원문/복수 target/청중 경로에
   연결할 수 있는가. MT와 TTS 준비 상태는 ASR와 독립적으로 판정한다.
2. Ming audio의 공개 checkpoint를 외부 추론 API 없이 실행할 수 있고,
   기존 로컬 ASR/MT 대비 의미 있는 이점이 있는가.
3. Ming TTS의 공식 0.5B 배포본을 CPU에서 합성할 수 있는가. 합성 성공,
   생성 속도, 발음·의미·음질 검증은 서로 다른 결과로 관리한다.

비용·개인정보 조건은 `paid_inference_apis=false`,
`external_ai_uploads=false`다. 모델/의존성 준비를 위한 다운로드는 세션
추론과 분리한다. 공개 checkpoint 다운로드를 회의 음성 업로드로
표현하지 않으며, Python 소켓 차단 검사를 전체 네트워크 패킷 감사라고
표현하지 않는다.

## 외부 근거와 고정 revision

| 대상 | 고정 근거 | 확인한 내용 | 아직 입증하지 않은 내용 |
|---|---|---|---|
| Ming audio | [모델 provenance](../knowledge/raw/2026-11-16-ming-audio-db94e0d.md) | 모델 카드의 126B total / 6.5B active 표기; 공식 HF metadata와 파일 크기 | 이 장비의 실행 성공, KO/JA ASR·번역 품질, 저사양 실시간 적합성 |
| Ming TTS | [모델 provenance](../knowledge/raw/2026-11-16-ming-tts-7247f4c.md) | 공개 weight/설정, 한국어·일본어 포함 언어 목록, 배포 구성요소 | 언어 목록만으로 실제 발음·음질 합격을 보장하지 않음 |
| Ming TTS runtime | [소스 provenance](../knowledge/raw/2026-11-16-ming-tts-runtime-a39b806.md) | CPU device 인자, BF16 loading, CUDA autocast 경로, 선택적인 flash-attn/TN | 공식 CPU/MPS 전체 경로 보장 또는 최소 GPU 요구량 |
| Apple | [기존 Apple MT 연구](../APPLE_SYSTEM_TRANSLATION.md), [Cicada 조사 #468](https://github.com/JDeun/LangTextFlow/issues/468) | Apple ASR와 text MT는 다른 API; 기존 LTF MT probe 존재 | Linux에서 Apple 모델 실행, private FaceTime/AirPods pipeline 재사용 |

모델 카드의 파라미터 표기와 실제 배포 파일 크기를 구분한다. TTS의
`0.5B`는 backbone을 설명하는 이름이며 배포본에는 DiT/audio tokenizer/
speaker encoder 등이 포함된다. 확인한 주 safetensors는
**2,959,055,504 bytes**이고 필요한 준비 파일 합은 **3,003,319,337 bytes**다.
Ming audio는 주 모델 25개 shard만 **256,370,360,248 bytes**, optional
talker를 포함한 safetensors 합은 **259,329,407,384 bytes**다.
이는 저장 파일 크기이며 정확한 runtime RAM/VRAM 최소값이 아니다.

## 역할별 실험과 Mac 우선 정책

실험 계획은 [수동 preregistration](../../benchmarks/cicada-local-speech-plan.v1.json)에
기록한다. 이 파일을 자동 컨베이어의 실행 DAG로 등록하지 않는다.

| 역할 | Mac에서 요청한 우선 경로 | 준비되지 않았을 때 |
|---|---|---|
| ASR | 지원되는 Apple silicon/macOS/locale에서 Apple Speech | 이미 구성한 다른 로컬 ASR 또는 명시적 unavailable |
| MT | 해당 source→target 자산이 설치된 Apple Translation | 구성된 로컬 MT. 원문을 번역 성공값으로 표시하지 않음 |
| TTS | 선택 언어의 설치된 Apple 시스템 voice | 음성 출력만 비활성; 자막 유지 |
| 회의록/교정 | 별도 준비한 로컬 LLM | 기록 유지, 해당 생성 기능 비활성 |

이 표는 **사용자가 정한 제품 방향**이며 현재 server의 AUTO 라우팅을
변경했다는 기록이 아니다. 구현 시 OS 이름만 확인해서 Apple을 선택하지
않고, 기능별로 다음을 확인한다.

- ASR: helper 존재·process architecture·OS/SDK·locale·설치 자산·관련 권한·실제 시작 성공.
- macOS 26+ ASR는 `SpeechAnalyzer`/`SpeechTranscriber`; Apple silicon의
  14.4–15 legacy 경로는 `supportsOnDeviceRecognition`과 요청별
  `requiresOnDeviceRecognition=true` 확인.
- Cicada의 x86_64 `cloudOnly`에서는 `isAvailable/status`가 서비스 사용을
  의미한다. 이 값을 Apple 로컬 ready로 복사하지 않는다.
- MT는 KO/EN/JA 여섯 방향을 각각 확인한다. 기존 headless probe는 macOS
  26+의 설치된 자산만 사용하며 `highFidelity` 요청과 실제 backend 확인을
  구분한다. ASR locale 지원이 MT 언어쌍 지원을 대신하지 않는다.
- TTS voice 및 notes 모델 준비 여부도 독립적으로 표시한다. Intel의 ASR
  제한을 모든 MT/TTS 기능의 불가 판정으로 확대하지 않는다.
- 어느 단계도 Google/Ling/Ming API로 자동 전환하지 않는다. 비교 실험은
  후보가 실패하면 실패로 기록하고 다른 엔진 출력으로 채우지 않는다.

## 재현 가능한 TTS 실행

[연구 runner](../../scripts/benchmark_ming_tts_local.py)는 이미 준비된
**절대 경로**의 model/source directory만 받는다. 실행 opt-in 이전에는
추론 의존성을 import하거나 가중치를 읽지 않는다. 실행 시 source SHA와
tracked clean 상태, 주 weight 및 CAMPPlus SHA256을 검증하고 다른
설정/tokenizer 파일의 hash도 기록한다.

준비 단계에서는 공식 source를 `a39b80660bdc5a4394d5e1d41af6dbfd9c443a54`,
HF checkpoint를 `7247f4c94a5b2e987404ed28a5cfec41b1a4ca61`로 고정한다.
별도 가상환경에 공식 `requirements.txt`를 설치하되 이번 CPU 실행은
`torch==2.6.0+cpu`와 `torchaudio==2.6.0+cpu`를 사용했다. 정확한 설치
버전은 결과 증거에 보존한다. 임의 모델 ID를 runtime에 전달하거나
추론 중 다운로드를 시도하지 않는다.

```bash
ORT_DISABLE_TELEMETRY=1 python scripts/benchmark_ming_tts_local.py \
  --source-dir /absolute/path/to/Ming-3.0-tts \
  --model-path /absolute/path/to/pinned-checkpoint \
  --output-dir /absolute/path/to/new-run-directory \
  --allow-model-execution --device cpu \
  --case en --max-decode-steps 128 --threads 4 --seed 1234
```

CPU dtype 호환성을 따로 검증할 때만 `--cpu-autocast`를 추가한다. 이는
공식 소스를 수정하지 않고 외부 CPU BF16 autocast context를 적용하는
**별도 실험 arm**이다. 기본 arm과 동일한 입력/seed/step bound를 사용한
비교를 보존하며 결과를 합치지 않는다. 한국어와 일본어는
`--case ko --case ja`로 별도 선택할 수 있다. 실제 사람 녹음 없이 영어는
built-in IP voice, KO/JA는 zero-speaker multilingual 입력을 사용한다.
reference voice를 사용한 공식 benchmark와 동일 조건이 아니다.

실행기는 Linux의 [native network guard](../../scripts/local_model_network_guard.py)를
**모델 dependency import 이전**에 설치한다. `libseccomp`의 thread-synchronized
filter가 socket/socketpair/connect/send 계열과 새 `io_uring` 생성을 거부하며,
libc를 직접 호출하는 IPv4/IPv6 TCP/UDP probe 네 가지로 설치를 확인한다.
설치가 실패하면 모델을 실행하지 않는다. 그 다음 `ORT_DISABLE_TELEMETRY=1`,
HF offline 환경변수와 Python audit hook을 적용하고, ONNX Runtime import
직후 `disable_telemetry_events()`를 호출한 뒤 Ming/Torch를 불러온다.

이 도구는 **새 Linux CLI 프로세스용**이다. 이미 열려 있던 socket/ring FD에
대한 모든 쓰기를 막는 완전한 sandbox가 아니므로 notebook·제품 process에
삽입하지 않는다. 이번 launcher는 `close_fds=True`와 file/pipe 표준 입출력을
사용하며 Python 시작 전에도 ORT 환경변수를 설정했다. 모델 실행의 CLI
선택지에 `mps`가 있더라도 현재 native guard는 Linux 전용이므로 Mac
Ming 실행을 지원하거나 검증했다는 뜻이 아니다.

Python audit 집계와 native 차단은 별개다. Python 차단 횟수 0은 native
telemetry 시도 0을 뜻하지 않으며 packet capture도 수행하지 않았다.
실행마다 새 output directory를 사용하고 실패/중단 기록을 덮어쓰지 않는다.
이번 실행의 launcher·resource limit·각 파일 hash는 evidence에 보존한다.

## 개인정보 관련 발견과 수정

초기 CPU 실험은 HF offline 환경변수와 Python socket audit만 적용했다.
추가 실행 중 **예상하지 않은 Microsoft telemetry 연결 때문에 자동 승인
검토가 실행을 거부했다.** 거부 사유는 사용자가 외부 전송을 허용하지 않았고,
해당 요청의 payload가 무해하다는 근거도 없다는 것이었다. 그 실행은 중단
기록으로 보존했으며, 같은 설정으로 재시도하지 않았다.

설치된 의존성은 `onnxruntime==1.32.0`이고 공식
[Privacy 문서](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md)는
지원되는 Linux 등을 포함한 native 배포의 기본 1DS telemetry와,
초기화 이전 `ORT_DISABLE_TELEMETRY=1` 설정을 설명한다. API disable만으로는
초기 이벤트를 막기 늦을 수 있다. 따라서 이 의존성이 관찰된 연결의 원인이라는
판단은 문서와 dependency 경로에 근거한 추론이며, payload를 캡처해 확인한
결과는 아니다. **어떤 정보가 전송되었는지, 전송이 끝났는지는 확인하지 못했다.**
이전 실행을 외부 전송 없는 구성의 성공 증거로 사용하지 않는다.

수정 후에는 환경변수/API 비활성화와 위 kernel 차단을 함께 사용했다.
네 가지 native probe 및 모델 import 순서·실패 시 중단을 포함한 19개
unit test가 통과했다. 이 검증은 특정 process의 차단 설치와 실행 경로에
대한 증거이며, 모든 배포/운영체제의 네트워크 감사나 개인정보 적합성
승인을 대신하지 않는다. 개인 회의 음성이나 비공개 전사문은 어느 실행에도
입력하지 않았으며, 유료 추론 API도 호출하지 않았다.

## 현재 실행 결과와 한계

실행 증거와 원본/공개본 hash는 [evidence index](evidence/ming-local-speech-2026-11-16/index.json)에
보존한다. [환경 사전점검](evidence/ming-local-speech-2026-11-16/environment-preflight.json),
[설치 의존성](evidence/ming-local-speech-2026-11-16/dependencies.json),
[수정 후 기본 CPU 오류](evidence/ming-local-speech-2026-11-16/isolated-zero-baseline-en.json),
[수정 후 CPU autocast 결과](evidence/ming-local-speech-2026-11-16/isolated-autocast-en-ko-ja.json)가
각각 별도 기록이다. 공개 JSON은 scratch prefix만 `<WORKSPACE>`로 치환했으며
기존 실패/중단 원본을 덮어쓰지 않았다.

[PR #469](https://github.com/JDeun/LangTextFlow/pull/469)의 최초 CI에서 guard의
import 뒤 빈 줄 하나에 Ruff I001이 발생해 형식만 수정했다.
[게시 후속 기록](evidence/ming-local-speech-2026-11-16/publication-followup.json)은
실제로 실행한 helper와 후속 형식 수정본의 hash를 구분한다. 실행 당시
소스는 commit `9448720c720483bc2c40b748dfa4785ff714c44d`에 보존되며,
이 형식 수정 때문에 모델을 다시 실행하거나 기존 결과 hash를 바꾸지 않았다.

실제 실행한 host는 Linux x86_64, vCPU 9개, 총 RAM 10,451,423,232 bytes,
CUDA 없음이다. torch는 4 threads, interop 1, upstream ONNX는 2 threads를
사용했다. CPU BF16 autocast, seed 1234, max decode 128, 언어별 한 문장,
warmup 없이 한 process에 EN→KO→JA 순서로 실행했다.

| 입력 | 음성 길이 | whole-waveform 생성 시간 | RTF | 결과 |
|---|---:|---:|---:|---|
| 영어 | 2.90초 | 22.60초 | 7.79 | finite/nonzero waveform 생성 |
| 한국어 | 2.00초 | 13.96초 | 6.98 | finite/nonzero waveform 생성 |
| 일본어 | 2.30초 | 16.86초 | 7.33 | finite/nonzero waveform 생성 |

RTF는 생성 시간÷생성 음성 길이다. 이 host/설정의 세 사례는 모두 1을
초과했으므로 실시간 출력 속도에 미달했다. 작은 표본으로 일반적인 p95,
다른 GPU·Mac 성능, 다른 모델 대비 순위를 주장하지 않는다. 모델 로드
1.62초와 dependency import 4.51초는 별도이며, OS 파일 cache가 비어 있는
cold boot 측정은 아니다. process peak RSS는 **2,545.21 MiB (약 2.49 GiB)**다.
이 값은 `ru_maxrss`의 process 누적 최고값으로 전체 시스템·자식 process·GPU
메모리와 다르며 low-memory Windows 적합성을 증명하지 않는다.

세 WAV는 [영어](evidence/ming-local-speech-2026-11-16/synthetic-en.wav),
[한국어](evidence/ming-local-speech-2026-11-16/synthetic-ko.wav),
[일본어](evidence/ming-local-speech-2026-11-16/synthetic-ja.wav)로 보존한다.
44.1kHz mono PCM16이며 별도 loudness normalization을 적용하지 않았다.
샘플 범위 밖/clipping은 세 사례 모두 0이지만 청취 품질 합격은 아니다.

기본 CPU+zero-speaker arm은 `Float`와 `BFloat16` 행렬 dtype 불일치로
합성 전에 실패했다. 외부 CPU BF16 autocast를 적용한 위 결과만 실행
호환성 증거로 사용한다. upstream repository는 고정 revision의 tracked
clean 상태로 유지했다. 최초 32-step EN 성공 및 이후 중단 run은 입력
조건·runner·privacy 제약이 달라 위 표와 비교·병합하지 않는다.

수정 후 실행 시작 전 plan SHA256은
`813d1a317fc9d45fa8ff7050fb2e01707694e40506a6f09d0c5d923298b0fdac`,
runner SHA256은
`a21890c9ed23c1080f6a33a9756a9243d05d2425adf75337e76a096ea4ab4b83`다.
[launcher 기록](evidence/ming-local-speech-2026-11-16/isolated-autocast-en-ko-ja-launch.json)은
360초 wall timeout, 8 GiB virtual address space 한도, process CPU 900초
한도를 명시한다. [실행 종료 기록](evidence/ming-local-speech-2026-11-16/isolated-autocast-en-ko-ja-outcome.json)은
exit 0, 전체 wall 64.28초, child user+system CPU 약 218.54초다.
이는 cgroup 단위의 aggregate descendants CPU cap이 아니며, 계획과의
이 차이는 launcher에 보존했다. 최초 탐색 실행을 소급 preregister하지 않았다.

측정 대상은 synthetic 문장의 whole-waveform TTS다. 첫 오디오 chunk
latency, 주관적 음질, 발음 정확도, ASR CER/WER, 번역 품질, 실제 사람
음성의 clone 유사도는 측정하지 않는다. 최대 decode step에 도달한
waveform은 문장 완성을 보장하지 않으므로 bounded smoke로 표시한다.

Ming audio는 현재 Linux CPU 약 10GB RAM/약 56GB 가용 디스크 환경에서
공식 원본 가중치 저장부터 불가능해 **BLOCKED**, weight download 및
추론을 시도하지 않았다. 6.5B active를 전체 가중치 메모리로 해석하지
않는다. 양자화·offload·GB10 등 다른 장비의 적합성은 별도 실험이다.

Apple은 Linux와 Swift 부재로 **NOT RUN**이다. 기존 MT runner의
non-macOS guard가 exit 2로 거절하는 것만 확인했다. 이를 native compile,
Apple ASR/MT 실행 성공 또는 시스템 모델 품질 증거로 집계하지 않는다.

## Mac에서 이어갈 검증

ASR helper는 #468의 bounded PCM/result IPC·PARTIAL/STABLE·revision/
cancel/reap 계약으로 별도 구현한다. 기존 [Apple MT probe](../../experiments/apple_translation/AppleTranslationProbe.swift)는
중복 작성하지 않고 실제 Mac에서 재사용한다.

```bash
xcrun swiftc -parse-as-library \
  experiments/apple_translation/AppleTranslationProbe.swift \
  -o /tmp/ltf-apple-translation-probe

python scripts/benchmark_apple_translation.py \
  --probe-bin /tmp/ltf-apple-translation-probe \
  --strategy lowLatency --allow-model-execution \
  --output artifacts/apple-translation-low-latency.json
```

이 명령은 설치된 언어 자산을 사용하는 **text MT** 검증이다. ASR와
별도로 KO/EN/JA 여섯 방향, 미설치 자산, 권한 거부, unsupported pair,
timeout, 외부 fallback 부재를 확인한다. 준비 이후 인터넷 차단 실측과
실제 행사 입력의 EN→KO·JA→KO acceptance를 남은 작업으로 유지한다.

## 채택 판단

- Ming TTS: 로컬 실행 후보로만 시험. 성공 여부와 무관하게 음성 출력은
  기존 자막/다중 대상 품질과 별개의 기능이다. 이번 CPU 설정은 호환성
  보정 후 waveform 생성에 성공했지만 실시간 속도/음질 합격은 아니다.
- Ming audio: 후보를 유지하되 현재 장비 실행은 차단. 지원 장비/정밀도/
  quantized checkpoint 검증 후 동일 PCM·독립 reference로 비교한다.
- Apple: 지원 Mac에서 기능별 로컬 우선이라는 제품 방향을 기록.
  helper·실장비·회귀 검증이 완료된 구현 PR에서 실제 default를 연결한다.
- 모든 arm은 `field_ready=false`. 첫 smoke 또는 테스트 통과로 모델이나
  출시를 승인하지 않으며 #106/#107의 저사양 Windows 조건도 유지한다.

## 라이선스·데이터

두 Ming 모델 카드와 공식 TTS repository의 MIT 표기, Cicada code의
Apache-2.0을 각각 기록한다. 각 dependency와 upstream에서 가져온 코드의
고지/조건은 별도로 보존한다. 이는 전체 배포 법률 검토 완료 표시가 아니다.
이번 smoke 입력은 새로 작성한 synthetic 문장이다. 개인 회의 음성,
비공개 전사문, API key는 사용하지 않는다. 모델 weight/음성 파일을 raw
provenance에 넣지 않고 hash와 재현 명령을 보존한다.
