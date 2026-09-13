import { useI18n } from "./i18n";
import { LANGUAGE_OPTIONS, languageLabel } from "./languages";
import { PANEL_COPY } from "./panelCopy";
import "./targetLanguages.css";

interface TargetLanguageSelectorProps {
  value: string[];
  sourceLanguage: string;
  disabled?: boolean;
  compact?: boolean;
  onChange: (languages: string[]) => void;
}

export function TargetLanguageSelector({
  value,
  sourceLanguage,
  disabled = false,
  compact = false,
  onChange,
}: TargetLanguageSelectorProps) {
  const { locale } = useI18n();
  const copy = PANEL_COPY[locale].target;
  const selected = new Set(value);
  const targets = LANGUAGE_OPTIONS.filter(([code]) => code !== sourceLanguage);

  function toggle(code: string) {
    if (disabled || code === sourceLanguage) return;
    if (selected.has(code)) {
      if (value.length <= 1) return;
      onChange(value.filter((item) => item !== code));
      return;
    }
    onChange([...value, code]);
  }

  return (
    <div className={`target-language-selector ${compact ? "compact" : ""}`}>
      <div className="target-language-heading">
        <span>{copy.heading}</span>
        <small>{value.length}{locale === "en" ? ` ${copy.selected}` : copy.selected}</small>
      </div>
      <div className="target-language-source" aria-label={copy.sourceAria}>
        <b>{sourceLanguage.toUpperCase()}</b>
        <span>{languageLabel(sourceLanguage)}</span>
        <i>{copy.sourceAlways}</i>
      </div>
      <div className="target-language-chips" role="group" aria-label={copy.groupAria}>
        {targets.map(([code, label]) => {
          const active = selected.has(code);
          const onlySelected = active && value.length === 1;
          return (
            <button
              key={code}
              type="button"
              className={active ? "active" : ""}
              aria-pressed={active}
              disabled={disabled || onlySelected}
              onClick={() => toggle(code)}
              title={onlySelected ? copy.minOne : label}
            >
              <b>{code.toUpperCase()}</b>
              <span>{label}</span>
            </button>
          );
        })}
      </div>
      {!compact && (
        <div className="target-language-summary">
          <span>
            {languageLabel(sourceLanguage)} {copy.sourceArrow} → {value.map(languageLabel).join(" · ")}
          </span>
          {value.length > 1 && <small>{copy.latencyHint}</small>}
        </div>
      )}
    </div>
  );
}
