import type { Locale } from "./locales";

export const OPERATOR_COPY: Record<Locale, {
  rulesOnly: string;
  correctionDefaultHelp: string;
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
}> = {
  en: {
    rulesOnly: "Rule-based correction only",
    correctionDefaultHelp: "Default: qwen3.5:4b · replace with another local instruction model if needed.",
    noTranslation: "No translation",
    translationRecommended: "Recommended starting point: translategemma:4b",
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
  },
  ko: {
    rulesOnly: "규칙 기반 보정만",
    correctionDefaultHelp: "기본값: qwen3.5:4b · 다른 로컬 instruction model로 교체 가능합니다.",
    noTranslation: "번역 사용 안 함",
    translationRecommended: "권장 시작점: translategemma:4b",
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
  },
  ja: {
    rulesOnly: "ルールベース補正のみ",
    correctionDefaultHelp: "既定値: qwen3.5:4b · 必要に応じて別のローカル instruction model に変更できます。",
    noTranslation: "翻訳しない",
    translationRecommended: "推奨開始モデル: translategemma:4b",
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
  },
};
