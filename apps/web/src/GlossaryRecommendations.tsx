import { useCallback, useEffect, useState } from "react";
import { API_URL } from "./api";
import { COMPLETION_COPY } from "./completionCopy";
import { useI18n } from "./i18n";
import type { ProductPreset } from "./types";

interface Recommendation {
  term: string;
  occurrences: number;
  sessions: number;
  score: number;
}

export function GlossaryRecommendations({
  preset,
  disabled,
}: {
  preset: ProductPreset;
  disabled: boolean;
}) {
  const { locale } = useI18n();
  const copy = COMPLETION_COPY[locale];
  const [items, setItems] = useState<Recommendation[]>([]);
  const [error, setError] = useState("");
  const [busyTerm, setBusyTerm] = useState("");

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/glossary/recommendations?limit=12`);
      if (!response.ok) throw new Error("glossary recommendation request failed");
      setItems((await response.json()) as Recommendation[]);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async (term: string) => {
    setBusyTerm(term);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/glossary`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          term,
          aliases: [],
          translations: {},
          category: "suggested",
          presets: [preset],
          boost: 1,
          enabled: true,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      setItems((current) => current.filter((item) => item.term !== term));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyTerm("");
    }
  };

  return (
    <section className="glossary-recommendations" data-testid="glossary-recommendations">
      <div className="section-heading-row">
        <div>
          <strong>{copy.recommendationsTitle}</strong>
          <small>{copy.recommendationsHelp}</small>
        </div>
        <button type="button" className="secondary-button" onClick={() => void refresh()}>{copy.refresh}</button>
      </div>
      {items.length === 0 ? (
        <small>{copy.recommendationsEmpty}</small>
      ) : (
        <div className="recommendation-list">
          {items.map((item) => (
            <div className="recommendation-row" key={item.term}>
              <div>
                <strong>{item.term}</strong>
                <small>{item.occurrences} {copy.occurrences} · {item.sessions} {copy.sessions}</small>
              </div>
              <button
                type="button"
                className="secondary-button"
                disabled={disabled || busyTerm === item.term}
                onClick={() => void add(item.term)}
              >
                {copy.add}
              </button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
    </section>
  );
}
