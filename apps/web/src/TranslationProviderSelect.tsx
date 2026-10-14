import { useI18n } from "./i18n";
import { messages } from "./translationProviderCopy";
import {
  providerBlockReason,
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
