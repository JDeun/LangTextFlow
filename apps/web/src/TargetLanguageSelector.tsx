import { LANGUAGE_OPTIONS, languageLabel } from "./languages";
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
        <span>번역 자막 언어</span>
        <small>{value.length}개 선택</small>
      </div>
      <div className="target-language-source" aria-label="원문 언어">
        <b>{sourceLanguage.toUpperCase()}</b>
        <span>{languageLabel(sourceLanguage)}</span>
        <i>원문 · 항상 제공</i>
      </div>
      <div className="target-language-chips" role="group" aria-label="번역 자막 언어 선택">
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
              title={onlySelected ? "번역 언어는 최소 1개가 필요합니다." : label}
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
            {languageLabel(sourceLanguage)} 원문 → {value.map(languageLabel).join(" · ")}
          </span>
          {value.length > 1 && (
            <small>대상 언어가 늘면 로컬 번역 호출 수와 segment 확정 지연도 증가할 수 있습니다.</small>
          )}
        </div>
      )}
    </div>
  );
}
