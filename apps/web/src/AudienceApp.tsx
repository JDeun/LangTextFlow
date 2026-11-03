import { useEffect, useState } from "react";
import { API_URL } from "./api";
import { AUDIENCE_METRICS_COPY } from "./audienceMetricsCopy";
import { captionTextForLanguage as displayCaption } from "./captionLanguage";
import { useI18n } from "./i18n";
import { captionLanguages, languageLabel } from "./languages";
import { LiveCaption } from "./LiveCaption";
import { useAudienceLatencyReporting } from "./useAudienceLatencyReporting";
import { useCaptionSocket } from "./useCaptionSocket";
import type { AudienceSessionView } from "./types";

export function AudienceApp({ joinCode, displayMode }: { joinCode: string; displayMode: boolean }) {
  const { t, locale } = useI18n();
  const { connected, segments, terminalError } = useCaptionSocket(
    `/ws/audience/${encodeURIComponent(joinCode)}`,
  );
  const [session, setSession] = useState<AudienceSessionView | null>(null);
  const [metricsConsent, setMetricsConsent] = useState(false);
  useAudienceLatencyReporting(API_URL, joinCode,
    metricsConsent && Boolean(session?.collect_audience_metrics) && connected && !displayMode);
  const [error, setError] = useState("");
  const queryLanguage = new URLSearchParams(window.location.search).get("lang") ?? "";
  const [language, setLanguage] = useState(queryLanguage);
  const latest = segments.at(-1);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/audience/${encodeURIComponent(joinCode)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(t("audience.invalidSession"));
        return (await response.json()) as AudienceSessionView;
      })
      .then((data) => {
        setSession(data);
        const available = captionLanguages(data.source_language, data.target_languages);
        setLanguage((current) => {
          if (current && available.includes(current)) return current;
          return data.target_languages[0] || data.source_language;
        });
      })
      .catch((reason: Error) => setError(reason.message));
  }, [joinCode]);

  if (error || terminalError) {
    return <main className="audience-error">{error || terminalError}</main>;
  }
  if (!session || !language) {
    return <main className="audience-error">{t("audience.loading")}</main>;
  }

  const availableLanguages = captionLanguages(session.source_language, session.target_languages);
  const displaySettings = session.display_settings;

  if (displayMode) {
    const mode = new URLSearchParams(window.location.search).get("mode") ?? "projector";
    return (
      <main className={`display-shell ${mode}`}>
        <LiveCaption
          segment={latest}
          language={language}
          settings={displaySettings}
          surface="display"
          primaryClassName="display-caption"
          sourceClassName="display-source"
        />
      </main>
    );
  }

  return (
    <main className="audience-shell">
      <header className="audience-header">
        <div>
          <div className="brand">{session.title}</div>
          <div className="subtitle">{session.presenter || "LangTextFlow Live"}</div>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="dot" /> {connected ? "LIVE" : t("audience.connecting")}
        </div>
      </header>
      <label className="audience-language">
        {t("audience.captionLanguage")}
        <select value={language} onChange={(event) => setLanguage(event.target.value)}>
          {availableLanguages.map((code) => (
            <option key={code} value={code}>
              {languageLabel(code)}{code === session.source_language ? ` · ${t("audience.source")}` : ""}
            </option>
          ))}
        </select>
      </label>
      {session.collect_audience_metrics && (
        <label>
          <input type="checkbox" checked={metricsConsent}
            data-testid="audience-metrics-consent"
            onChange={(event) => setMetricsConsent(event.target.checked)} />
          {AUDIENCE_METRICS_COPY[locale].audienceConsent}
        </label>
      )}
      <section className="audience-caption-card">
        <LiveCaption
          segment={latest}
          language={language}
          settings={displaySettings}
          surface="audience"
          primaryClassName="audience-caption"
          sourceClassName="audience-source"
        />
      </section>
      <section className="audience-transcript">
        {segments.slice(-8).reverse().map((segment) => (
          <article key={segment.segment_id}>
            <span>{displayCaption(segment, language, t("live.emptyCaption"))}</span>
            {segment.stage === "partial" && <small>draft</small>}
          </article>
        ))}
      </section>
    </main>
  );
}

