import type { CSSProperties } from "react";
import type {
  CaptionDisplaySettings,
  CaptionFontFamily,
  CaptionTextAlign,
} from "./types";

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

const FONT_VALUES = new Set<CaptionFontFamily>(["system", "sans", "serif", "mono"]);
const ALIGN_VALUES = new Set<CaptionTextAlign>(["left", "center"]);

export type CaptionSurface = keyof typeof BASE_REM;

function boundedNumber(value: unknown, fallback: number, min: number, max: number) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
}

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
    if (!raw) return { ...DEFAULT_DISPLAY_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<CaptionDisplaySettings>;
    const font = FONT_VALUES.has(parsed.font_family as CaptionFontFamily)
      ? parsed.font_family as CaptionFontFamily
      : DEFAULT_DISPLAY_SETTINGS.font_family;
    const align = ALIGN_VALUES.has(parsed.text_align as CaptionTextAlign)
      ? parsed.text_align as CaptionTextAlign
      : DEFAULT_DISPLAY_SETTINGS.text_align;
    return {
      font_family: font,
      font_scale_percent: boundedNumber(
        parsed.font_scale_percent,
        DEFAULT_DISPLAY_SETTINGS.font_scale_percent,
        70,
        180,
      ),
      max_lines: boundedNumber(parsed.max_lines, DEFAULT_DISPLAY_SETTINGS.max_lines, 1, 4),
      hold_seconds: boundedNumber(
        parsed.hold_seconds,
        DEFAULT_DISPLAY_SETTINGS.hold_seconds,
        0,
        30,
      ),
      show_source_when_translated:
        typeof parsed.show_source_when_translated === "boolean"
          ? parsed.show_source_when_translated
          : DEFAULT_DISPLAY_SETTINGS.show_source_when_translated,
      text_align: align,
    };
  } catch {
    return { ...DEFAULT_DISPLAY_SETTINGS };
  }
}
