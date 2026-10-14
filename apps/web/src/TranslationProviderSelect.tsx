import { useI18n } from "./i18n";
import {
  providerBlockReason,
  type ProviderBlockReason,
  type TranslationCapability,
  type TranslationRegistry,
} from "./translationCapabilities";

interface Props {
  value: string;
  onChange: (provider: string) => void;
  disabled?: boolean;
  sourceLanguage: string;
  targetLanguages: string[];
  engine: string;
  registry: TranslationRegistry;
}

const fallbackNone: TranslationCapability = {
  provider: "none", label: "No translation", local_only: true,
  requires_model: false, supported_pairs: [], configured: true,
  field_qualified: false, glossary_mode: "unsupported",
  model_id: null, warnings: [],
};

const messages: Record<"ko" | "en" | "ja", Record<Exclude<ProviderBlockReason, null>, string>> = {
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

export function TranslationProviderSelect({
  value, onChange, disabled = false, sourceLanguage, targetLanguages, engine, registry,
}: Props) {
  const { locale } = useI18n();
  const list = registry.status === "ready" ? registry.providers : [fallbackNone];
  const visible = list.filter((item) => item.provider !== "demo" || engine === "mock");
  // Preserve the controlled value without misrepresenting an absent provider
  // as installed: it stays visibly disabled until discovery succeeds.
  const chosen = visible.some((item) => item.provider === value)
    ? visible
    : [{
      ...fallbackNone, provider: value,
      label: `${value} (unavailable)`,
      configured: false,
    }, ...visible];
  const reason = providerBlockReason(registry, value, sourceLanguage, targetLanguages, engine);
  const active = registry.status === "ready"
    ? registry.providers.find((item) => item.provider === value)
    : null;
  return (
    <>
      <select
        data-testid="translation-provider-select"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {chosen.map((item) => {
          const blocked = providerBlockReason(
            registry, item.provider, sourceLanguage, targetLanguages, engine,
          );
          return (
            <option key={item.provider} value={item.provider} disabled={blocked !== null}>
              {item.label}
              {blocked === "missing" ? " (not installed)" : ""}
              {blocked === "pair" ? " (unsupported pair)" : ""}
            </option>
          );
        })}
      </select>
      {reason && <small role="status">{messages[locale][reason]}</small>}
      {active?.warnings && active.warnings.length > 0 && !reason && (
        <small role="status">{active.warnings[0]}</small>
      )}
    </>
  );
}
