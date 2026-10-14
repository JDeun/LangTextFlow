import type { ProviderBlockReason } from "./translationCapabilities";

/** Localized operator-facing diagnostics, kept outside React components. */
export const messages: Record<"ko" | "en" | "ja", Record<Exclude<ProviderBlockReason, null>, string>> = {
  ko: {
    registry: "번역 엔진 정보를 확인할 수 없습니다. 원문 자막만 사용할 수 있습니다.",
    unknown: "현재 서버에서 이 번역 엔진을 지원하지 않습니다.",
    demo: "데모 번역기는 데모 ASR 모드에서만 선택할 수 있습니다.",
    missing: "이 번역 엔진의 로컬 모델 또는 필수 구성 요소가 설치되지 않았습니다.",
    pair: "선택한 입력·출력 언어 조합을 지원하지 않는 번역 엔진입니다.",
  },
  en: {
    registry: "Provider discovery unavailable; source captions only.",
    unknown: "This provider is not declared by the current server.",
    demo: "Demo translation requires demo ASR mode.",
    missing: "The local model or required dependency is not installed.",
    pair: "The selected input/output language pair is unsupported.",
  },
  ja: {
    registry: "翻訳エンジン情報を取得できません。原文字幕のみ使用できます。",
    unknown: "この翻訳エンジンは現在のサーバーで未対応です。",
    demo: "デモ翻訳にはデモ音声認識モードが必要です。",
    missing: "ローカルモデルまたは必須の依存関係がありません。",
    pair: "選択した入力・出力言語の組み合わせは未対応です。",
  },
};

