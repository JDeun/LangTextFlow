import { DEFAULT_DISPLAY_SETTINGS } from "./displaySettings";
import type { CaptionDisplaySettings, CaptionFontFamily, CaptionTextAlign } from "./types";
import "./displaySettings.css";

interface DisplaySettingsPanelProps {
  value: CaptionDisplaySettings;
  disabled?: boolean;
  onChange: (value: CaptionDisplaySettings) => void;
}

const FONT_OPTIONS: Array<[CaptionFontFamily, string]> = [
  ["system", "시스템 기본"],
  ["sans", "Sans"],
  ["serif", "Serif"],
  ["mono", "Monospace"],
];

export function DisplaySettingsPanel({
  value,
  disabled = false,
  onChange,
}: DisplaySettingsPanelProps) {
  function patch(update: Partial<CaptionDisplaySettings>) {
    onChange({ ...value, ...update });
  }

  return (
    <details className="display-settings panel-section">
      <summary>
        <span>
          <strong>자막 표시 설정</strong>
          <small>프로젝터 · OBS · 청중 화면</small>
        </span>
        <b>{value.font_scale_percent}% · {value.max_lines}줄</b>
      </summary>

      <div className="display-settings-body">
        <label>
          글꼴
          <select
            value={value.font_family}
            disabled={disabled}
            onChange={(event) => patch({ font_family: event.target.value as CaptionFontFamily })}
          >
            {FONT_OPTIONS.map(([font, label]) => <option key={font} value={font}>{label}</option>)}
          </select>
        </label>

        <label className="display-range">
          <span>자막 크기 <b>{value.font_scale_percent}%</b></span>
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
            최대 행수
            <select
              value={value.max_lines}
              disabled={disabled}
              onChange={(event) => patch({ max_lines: Number(event.target.value) })}
            >
              {[1, 2, 3, 4].map((count) => <option key={count} value={count}>{count}줄</option>)}
            </select>
          </label>
          <label>
            정렬
            <select
              value={value.text_align}
              disabled={disabled}
              onChange={(event) => patch({ text_align: event.target.value as CaptionTextAlign })}
            >
              <option value="center">가운데</option>
              <option value="left">왼쪽</option>
            </select>
          </label>
        </div>

        <label className="display-range">
          <span>
            자막 유지시간
            <b>{value.hold_seconds === 0 ? "계속" : `${value.hold_seconds}초`}</b>
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
          <small>0초는 다음 자막이 올 때까지 현재 자막을 유지합니다.</small>
        </label>

        <label className="display-toggle">
          <input
            type="checkbox"
            checked={value.show_source_when_translated}
            disabled={disabled}
            onChange={(event) => patch({ show_source_when_translated: event.target.checked })}
          />
          <span>
            <strong>번역 아래 원문 함께 표시</strong>
            <small>번역 자막을 선택했을 때 원문을 보조 줄로 표시합니다.</small>
          </span>
        </label>

        <button
          type="button"
          className="secondary-button display-reset"
          disabled={disabled}
          onClick={() => onChange({ ...DEFAULT_DISPLAY_SETTINGS })}
        >
          기본값으로 복원
        </button>
      </div>
    </details>
  );
}
