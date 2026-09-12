import { useEffect, useState } from "react";
import {
  captionTextStyle,
  sourceTextStyle,
  type CaptionSurface,
} from "./displaySettings";
import type { CaptionDisplaySettings, TranscriptEvent } from "./types";

interface LiveCaptionProps {
  segment?: TranscriptEvent;
  language: string;
  settings: CaptionDisplaySettings;
  surface: CaptionSurface;
  primaryClassName: string;
  sourceClassName?: string;
  emptyText?: string;
}

function textForLanguage(segment: TranscriptEvent, language: string) {
  if (language === segment.source_language) return segment.text;
  return segment.translations[language] || segment.text;
}

export function LiveCaption({
  segment,
  language,
  settings,
  surface,
  primaryClassName,
  sourceClassName,
  emptyText = "",
}: LiveCaptionProps) {
  const [visible, setVisible] = useState(Boolean(segment));

  useEffect(() => {
    if (!segment) {
      setVisible(false);
      return;
    }

    setVisible(true);
    if (!segment.committed || settings.hold_seconds <= 0) return;

    const emittedAt = Date.parse(segment.emitted_at);
    const baseTime = Number.isFinite(emittedAt) ? emittedAt : Date.now();
    const delayMs = baseTime + settings.hold_seconds * 1000 - Date.now();
    if (delayMs <= 0) {
      setVisible(false);
      return;
    }

    const timer = window.setTimeout(() => setVisible(false), delayMs);
    return () => window.clearTimeout(timer);
  }, [
    segment?.segment_id,
    segment?.version,
    segment?.committed,
    segment?.emitted_at,
    settings.hold_seconds,
  ]);

  const translated = Boolean(
    segment
    && language !== segment.source_language
    && segment.translations[language],
  );
  const primaryText = segment && visible ? textForLanguage(segment, language) : emptyText;

  return (
    <>
      <div
        className={`${primaryClassName} ${segment?.stage === "partial" ? "draft" : ""}`.trim()}
        style={captionTextStyle(settings, surface)}
      >
        {primaryText}
      </div>
      {segment
        && visible
        && translated
        && settings.show_source_when_translated
        && sourceClassName && (
          <div className={sourceClassName} style={sourceTextStyle(settings)}>
            {segment.text}
          </div>
        )}
    </>
  );
}
