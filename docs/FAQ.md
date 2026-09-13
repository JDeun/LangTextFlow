# 자주 묻는 질문

## LangTextFlow는 무엇인가요?

말하는 내용을 실시간 자막으로 만들고 필요한 언어로 번역해 휴대폰, 프로젝터, OBS 등에 전달하는 local-first 오픈소스 앱입니다.

## 어떤 상황에서 사용할 수 있나요?

다국어 예배/선교 집회, 국제 컨퍼런스, 강의, 세미나, 사내 행사, 라이브 스트리밍처럼 한 사람이 말하고 여러 사람이 자막을 읽는 환경을 주요 대상으로 합니다.

## 인터넷이 꼭 필요한가요?

아닙니다. 로컬 ASR과 Ollama 같은 로컬 번역 구성을 사용하면 핵심 처리 경로를 로컬에서 구성할 수 있습니다. 외부 OpenAI-compatible provider를 선택하면 해당 기능에는 네트워크가 필요합니다.

## 청중도 앱을 설치해야 하나요?

Audience 경로는 브라우저에서 접속하는 방식으로 설계되어 있습니다. 같은 네트워크에서 QR/join 정보를 통해 자막을 볼 수 있습니다.

## 한국어만 지원하나요?

아닙니다. 인터페이스는 현재 English, 한국어, 日本語를 기본 지원하며 음성 인식/번역 언어 범위는 선택한 모델과 provider의 지원 범위에 따라 달라집니다.

## 여러 번역 언어를 동시에 보여줄 수 있나요?

네. 하나의 source caption에서 여러 target language로 번역 fan-out할 수 있습니다.

## 자막을 저장할 수 있나요?

네. 세션 기록은 로컬 SQLite에 저장되며 SRT, WebVTT, TXT, JSON으로 내보낼 수 있습니다.

## OBS나 프로젝터에도 사용할 수 있나요?

네. Audience와 별도로 Projector/OBS용 표시 surface와 공통 Display Profile을 제공합니다.

## 전문 용어가 많은 행사에도 사용할 수 있나요?

Hotword, Glossary, reference document를 이용해 고유명사와 전문 용어의 문맥을 제공할 수 있습니다.

## Cuckoo 같은 서비스와 무엇이 다른가요?

LangTextFlow는 특정 상용 서비스를 복제하는 프로젝트가 아니라, 실시간 다국어 caption/live-event 문제를 local-first·open-source 방식으로 해결하는 독립 프로젝트입니다. 모델/provider 선택, self-host/local processing, 운영 상태와 failover를 사용자에게 더 투명하게 제공하는 것을 중요하게 봅니다.

## 완전히 상용 배포 가능한 상태인가요?

아직 실제 현장 acceptance 전 단계입니다. 코드·자동 테스트·보안/접근성/패키징 경로를 준비하더라도 실제 음향 환경, 장비, 번역 품질, 장시간 안정성은 현장 검증이 필요합니다.

## 개발자가 아니어도 사용할 수 있나요?

그것이 제품 목표입니다. Onboarding, Preflight, 모델 준비/복구 UI와 desktop packaging을 통해 터미널 의존성을 줄이는 방향으로 개발합니다. 현재 공개 배포 상태와 설치 방법은 README의 최신 상태 안내를 확인하십시오.
