import { DEFAULT_DISPLAY_SETTINGS } from "./displaySettings";
import { useI18n } from "./i18n";
import { PANEL_COPY } from "./panelCopy";
import type { CaptionDisplaySettings, CaptionFontFamily, CaptionTextAlign } from "./types";
import "./displaySettings.css";

interface DisplaySettingsPanelProps {
  value: CaptionDisplaySettings;
  disabled?: boolean;
  onChange: (value: CaptionDisplaySettings) => void;
}

export function DisplaySettingsPanel({
  value,
  disabled = false,
  onChange,
}: DisplaySettingsPanelProps) {
  const { locale } = useI18n();
  const copy = PANEL_COPY[locale].display;
  const fontOptions: Array<[CaptionFontFamily, string]> = [
    ["system", copy.systemFont],
    ["sans", "Sans"],
    ["serif", "Serif"],
    ["mono", "Monospace"],
  ];

  function patch(update: Partial<CaptionDisplaySettings>) {
    onChange({ ...value, ...update });
  }

  return (
    <details className="display-settings panel-section">
      <summary>
        <span>
          <strong>{copy.title}</strong>
          <small>{copy.surfaces}</small>
        </span>
        <b>{value.font_scale_percent}% · {value.max_lines}{copy.lines}</b>
      </summary>

      <div className="display-settings-body">
        <label>
          {copy.font}
          <select
            value={value.font_family}
            disabled={disabled}
            onChange={(event) => patch({ font_family: event.target.value as CaptionFontFamily })}
          >
            {fontOptions.map(([font, label]) => <option key={font} value={font}>{label}</option>)}
          </select>
        </label>

        <label className="display-range">
          <span>{copy.size} <b>{value.font_scale_percent}%</b></span>
          <input
            type="range"
            min="70"
            max="180"
            step="10"
            value={value.font_scale_percent}
            disabled={disabled}
            onChange={(event) => patch({ font_scale_percent: Number(event.target.value) })}
          />
        </label>

        <div className="display-settings-grid">
          <label>
            {copy.maxLines}
            <select
              value={value.max_lines}
              disabled={disabled}
              onChange={(event) => patch({ max_lines: Number(event.target.value) })}
            >
              {[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count}{copy.lines}</option>)}
            </select>
          </label>
          <label>
            {copy.align}
            <select
              value={value.text_align}
              disabled={disabled}
              onChange={(event) => patch({ text_align: event.target.value as CaptionTextAlign })}
            >
              <option value="center">{copy.center}</option>
              <option value="left">{copy.left}</option>
            </select>
          </label>
        </div>

        <label className="display-range">
          <span>
            {copy.hold}
            <b>{value.hold_seconds === 0 ? copy.continuous : `${value.hold_seconds}${copy.seconds}`}</b>
          </span>
          <input
            type="range"
            min="0"
            max="30"
            step="1"
            value={value.hold_seconds}
            disabled={disabled}
            onChange={(event) => patch({ hold_seconds: Number(event.target.value) })}
          />
          <small>{copy.holdHelp}</small>
        </label>

        <label className="display-toggle">
          <input
            type="checkbox"
            checked={value.show_source_when_translated}
            disabled={disabled}
            onChange={(event) => patch({ show_source_when_translated: event.target.checked })}
          />
          <span>
            <strong>{copy.showSource}</strong>
            <small>{copy.showSourceHelp}</small>
          </span>
        </label>

        <button
          type="button"
          className="secondary-button display-reset"
          disabled={disabled}
          onClick={() => onChange({ ...DEFAULT_DISPLAY_SETTINGS })}
        >
          {copy.reset}
        </button>
      </div>
    </details>
  );
}
