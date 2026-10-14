import { useI18n } from "./i18n";
import { asrAvailabilityCopy } from "./asrCapabilityCopy";
import { asrBlockReason, type AsrRegistry } from "./asrCapabilities";

interface Props {
  value: string;
  onChange: (value: string) => void;
  registry: AsrRegistry;
  sourceLanguage: string;
  disabled?: boolean;
}

export function AsrProviderSelect({
  value, onChange, registry, sourceLanguage, disabled = false,
}: Props) {
  const { locale } = useI18n();
  const providers = registry.status === "ready" ? registry.providers : [];
  const present = providers.some((item) => item.provider === value);
  const reason = asrBlockReason(registry, value, sourceLanguage);
  const selected = providers.find((item) => item.provider === value);
  return (
    <>
      <select
        data-testid="engine-select"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {!present && (
          <option value={value} disabled>{value} (unavailable)</option>
        )}
        {providers.map((item) => (
          <option
            value={item.provider}
            key={item.provider}
            disabled={asrBlockReason(registry, item.provider, sourceLanguage) !== null}
          >
            {item.label}
          </option>
        ))}
      </select>
      {reason && <small role="status">{asrAvailabilityCopy[locale][reason]}</small>}
      {!reason && selected?.warnings[0] && (
        <small role="status">{selected.warnings[0]}</small>
      )}
    </>
  );
}
