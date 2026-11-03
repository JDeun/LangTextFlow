import type { Locale } from "./locales";

export const OPERATOR_COPY: Record<Locale, {
  rulesOnly: string;
  configuredCorrection: string;
  correctionDefaultHelp: string;
  configuredDefaultModel: string;
  noTranslation: string;
  translationRecommended: string;
  exactModelId: string;
  correctionStatus: string;
  translationStatus: string;
  contextGroup: string;
  contextGroupHelp: string;
  advancedGroup: string;
  advancedGroupHelp: string;
  ready: string;
  deterministicFallback: string;
  unavailable: string;
  unknown: string;
  resourceLabel: string;
  resourceStandard: string;
  resourceLow: string;
  resourceHelp: string;
  projectLabel: string;
  projectNone: string;
  recoveredAudio: string;
}> = {
  en: {
    rulesOnly: "Rule-based correction only",
    configuredCorrection: "Configured Ollama correction",
    correctionDefaultHelp: "Leave blank for the configured model. Source transcript text is sent to the configured Ollama endpoint; verify whether it is local or remote before enabling correction.",
    configuredDefaultModel: "Configured default model",
    noTranslation: "No translation",
    translationRecommended: "Leave blank to use this provider's configured model. Verify its languages and quality before use.",
    exactModelId: "Enter the exact model id returned by /v1/models.",
    correctionStatus: "Correction",
    translationStatus: "Translation",
    contextGroup: "Context & terminology",
    contextGroupHelp: "Hotwords, reference documents, and reusable glossary entries",
    advancedGroup: "Engine & model settings",
    advancedGroupHelp: "ASR, correction, translation, and local model configuration",
    ready: "ready",
    deterministicFallback: "deterministic fallback",
    unavailable: "unavailable",
    unknown: "unknown",
    resourceLabel: "Hardware mode", resourceStandard: "Standard",
    resourceLow: "Low-spec CPU (source captions)",
    resourceHelp: "Avoids auto-starting 7B ASR and 4B translation. Translated captions require a separately verified lightweight translator.",
    projectLabel: "Event project", projectNone: "No project",
    recoveredAudio: "The running session was restored. This tab has not restarted its microphone; stop the session before starting fresh audio here.",
  },
  ko: {
    rulesOnly: "규칙 기반 보정만",
    configuredCorrection: "설정된 Ollama 보정",
    correctionDefaultHelp: "비워두면 설정된 보정 모델을 사용합니다. 원문 전사문이 설정된 Ollama 주소로 전송됩니다. 보정을 켜기 전에 로컬·원격 주소인지 확인하세요.",
    configuredDefaultModel: "서버에 설정된 기본 모델",
    noTranslation: "번역 사용 안 함",
    translationRecommended: "비워두면 이 번역 엔진에 설정된 모델을 사용합니다. 사용 전 언어 지원과 품질을 확인하세요.",
    exactModelId: "서버의 /v1/models가 반환하는 정확한 model id를 입력하세요.",
    correctionStatus: "보정",
    translationStatus: "번역",
    contextGroup: "문맥 & 용어",
    contextGroupHelp: "Hotwords, 참고 문서, 반복 사용하는 용어집",
    advancedGroup: "엔진 & 모델 설정",
    advancedGroupHelp: "ASR, 보정, 번역 및 로컬 모델 고급 설정",
    ready: "준비됨",
    deterministicFallback: "규칙 기반 fallback",
    unavailable: "사용 불가",
    unknown: "알 수 없음",
    resourceLabel: "PC 사양에 맞게 선택", resourceStandard: "표준 모드",
    resourceLow: "저사양 CPU 모드 (원문 자막)",
    resourceHelp: "7B ASR·4B 번역 모델을 자동 실행하지 않습니다. 다국어 통역은 검증된 경량 번역기를 별도로 구성해야 합니다.",
    projectLabel: "행사 프로젝트", projectNone: "프로젝트 없이 진행",
    recoveredAudio: "실행 중인 세션을 복원했습니다. 이 탭의 마이크는 자동 재시작하지 않았습니다. 여기서 새 음성을 보내려면 먼저 세션을 중지하세요.",
  },
  ja: {
    rulesOnly: "ルールベース補正のみ",
    configuredCorrection: "設定済みOllama補正",
    correctionDefaultHelp: "空欄なら設定済みの補正モデルを使用します。原文は設定済みのOllamaエンドポイントへ送信されます。補正を有効にする前に、ローカルかリモートかを確認してください。",
    configuredDefaultModel: "サーバーに設定済みのモデル",
    noTranslation: "翻訳しない",
    translationRecommended: "空欄ならプロバイダーの設定済みモデルを使用します。使用前に対応言語と品質を確認してください。",
    exactModelId: "サーバーの /v1/models が返す正確な model id を入力してください。",
    correctionStatus: "補正",
    translationStatus: "翻訳",
    contextGroup: "コンテキスト & 用語",
    contextGroupHelp: "Hotwords、参考文書、再利用する用語集",
    advancedGroup: "エンジン & モデル設定",
    advancedGroupHelp: "ASR、補正、翻訳、ローカルモデルの詳細設定",
    ready: "準備完了",
    deterministicFallback: "ルールベース fallback",
    unavailable: "利用不可",
    unknown: "不明",
    resourceLabel: "PC の性能", resourceStandard: "標準モード",
    resourceLow: "低スペック CPU (原文字幕)",
    resourceHelp: "7B ASR・4B 翻訳モデルの自動起動を避けます。翻訳字幕は別途軽量翻訳モデルの設定が必要です。",
    projectLabel: "イベントプロジェクト", projectNone: "プロジェクトなし",
    recoveredAudio: "実行中のセッションを復元しました。このタブのマイクは自動再開していません。ここから新しい音声を送るには、先にセッションを停止してください。",
  },
};
