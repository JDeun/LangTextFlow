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

  function toggle(code: string) {
    if (disabled) return;
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
        <span>자막 언어</span>
        <small>{value.length}개 선택</small>
      </div>
      <div className="target-language-chips" role="group" aria-label="자막 언어 선택">
        {LANGUAGE_OPTIONS.map(([code, label]) => {
          const active = selected.has(code);
          const sameAsSource = code === sourceLanguage;
          const onlySelected = active && value.length === 1;
          return (
            <button
              key={code}
              type="button"
              className={active ? "active" : ""}
              aria-pressed={active}
              disabled={disabled || onlySelected}
              onClick={() => toggle(code)}
              title={
                onlySelected
                  ? "자막 언어는 최소 1개가 필요합니다."
                  : sameAsSource
                    ? `${label}: 원문 자막으로 표시됩니다.`
                    : label
              }
            >
              <b>{code.toUpperCase()}</b>
              <span>{label}</span>
              {sameAsSource && <i>원문</i>}
            </button>
          );
        })}
      </div>
      {!compact && (
        <div className="target-language-summary">
          <span>{value.map(languageLabel).join(" · ")}</span>
          {value.length > 1 && (
            <small>대상 언어가 늘면 로컬 번역 호출 수와 segment 확정 지연도 증가할 수 있습니다.</small>
          )}
        </div>
      )}
    </div>
  );
}
