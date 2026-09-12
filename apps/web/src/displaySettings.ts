import type { CSSProperties } from "react";
import type { CaptionDisplaySettings, CaptionFontFamily } from "./types";

export const DEFAULT_DISPLAY_SETTINGS: CaptionDisplaySettings = {
  font_family: "system",
  font_scale_percent: 100,
  max_lines: 2,
  hold_seconds: 8,
  show_source_when_translated: true,
  text_align: "center",
};

const FONT_STACKS: Record<CaptionFontFamily, string> = {
  system: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  sans: 'Arial, "Noto Sans", sans-serif',
  serif: 'Georgia, "Noto Serif", serif',
  mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
};

const BASE_REM = {
  audience: 2.05,
  preview: 2.45,
  display: 4.5,
} as const;

export type CaptionSurface = keyof typeof BASE_REM;

export function captionTextStyle(
  settings: CaptionDisplaySettings,
  surface: CaptionSurface,
): CSSProperties {
  const scale = settings.font_scale_percent / 100;
  return {
    fontFamily: FONT_STACKS[settings.font_family],
    fontSize: `${(BASE_REM[surface] * scale).toFixed(3)}rem`,
    lineHeight: 1.24,
    textAlign: settings.text_align,
    display: "-webkit-box",
    WebkitBoxOrient: "vertical",
    WebkitLineClamp: settings.max_lines,
    overflow: "hidden",
  };
}

export function sourceTextStyle(settings: CaptionDisplaySettings): CSSProperties {
  return {
    fontFamily: FONT_STACKS[settings.font_family],
    textAlign: settings.text_align,
  };
}

export function loadStoredDisplaySettings(storageKey: string): CaptionDisplaySettings {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return DEFAULT_DISPLAY_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<CaptionDisplaySettings>;
    return {
      ...DEFAULT_DISPLAY_SETTINGS,
      ...parsed,
      font_scale_percent: Math.min(180, Math.max(70, Number(parsed.font_scale_percent) || 100)),
      max_lines: Math.min(4, Math.max(1, Number(parsed.max_lines) || 2)),
      hold_seconds: Math.min(30, Math.max(0, Number(parsed.hold_seconds) || 0)),
    };
  } catch {
    return DEFAULT_DISPLAY_SETTINGS;
  }
}
