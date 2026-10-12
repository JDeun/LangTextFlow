import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_SEARCH_COPY } from "./projectSearchCopy";

interface TranscriptSearchHit {
  project_id: string;
  session_id: string;
  session_title: string;
  segment_id: string;
  start_ms: number;
  end_ms: number | null;
  speaker: string | null;
  source_language: string;
  original_text: string;
  matched_language: string;
  matched_text: string;
}

interface Props {
  apiUrl: string;
  projectId: string;
  locale: Locale;
  onOpenSession: (sessionId: string, matchedText: string) => void;
}


function timestamp(value: number) {
  const seconds = Math.floor(Math.max(0, value) / 1000);
  const hours = Math.floor(seconds / 3600);
  const minute = Math.floor(seconds / 60) % 60;
  const second = seconds % 60;
  return hours
    ? `${hours}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`
    : `${minute}:${String(second).padStart(2, "0")}`;
}

export function ProjectTranscriptSearch({
  apiUrl, projectId, locale, onOpenSession,
}: Props) {
  const copy = PROJECT_SEARCH_COPY[locale];
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<TranscriptSearchHit[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestRef = useRef(0);
  const [hasMore, setHasMore] = useState(false);

  useEffect(() => {
    requestRef.current += 1;
    setQuery("");
    setMatches([]);
    setSubmitted(false);
    setHasMore(false);
    setBusy(false);
    setError("");
  }, [projectId]);

  async function find(nextOffset = 0) {
    const phrase = query.trim();
    if (phrase.length < 2 || phrase.length > 128) return;
    const requestId = ++requestRef.current;
    setBusy(true);
    setError("");
    try {
      const params = new URLSearchParams({
        q: phrase,
        limit: "50",
        offset: String(nextOffset),
      });
      const response = await fetch(
        `${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/search?${params}`,
      );
      if (!response.ok) throw new Error(await response.text());
      const next = (await response.json()) as TranscriptSearchHit[];
      if (requestId !== requestRef.current) return;
      setMatches((old) => nextOffset ? [...old, ...next] : next);
      setHasMore(next.length === 50 && nextOffset + next.length < 10_000);
      setSubmitted(true);
    } catch (reason) {
      if (requestId === requestRef.current) {
        setError(reason instanceof Error ? reason.message : copy.error);
      }
    } finally {
      if (requestId === requestRef.current) setBusy(false);
    }
  }

  return (
    <section className="project-transcript-search" aria-label={copy.heading}>
      <div>
        <h3>{copy.heading}</h3>
        <small>{copy.helper}</small>
      </div>
      <form
        className="project-transcript-form"
        onSubmit={(event) => { event.preventDefault(); void find(); }}
      >
        <input
          type="search"
          value={query}
          maxLength={128}
          aria-label={copy.heading}
          placeholder={copy.placeholder}
          onChange={(event) => {
            // Invalidate in-flight searches before showing results for a new query.
            requestRef.current += 1;
            setQuery(event.target.value);
            setSubmitted(false);
            setBusy(false);
            setMatches([]);
            setHasMore(false);
          }}
        />
        <button type="submit" disabled={busy || query.trim().length < 2}>
          {copy.search}
        </button>
      </form>
      {busy && <span role="status">{copy.search}…</span>}
      {error && <p role="alert" className="error-box">{error}</p>}
      {submitted && !matches.length && <p>{copy.noResults}</p>}
      {matches.length > 0 && (
        <ol className="project-transcript-results">
          {matches.map((hit) => (
            <li key={`${hit.session_id}:${hit.segment_id}`}>
              <div className="project-transcript-result-meta">
                <strong>{hit.session_title}</strong>
                <small>{timestamp(hit.start_ms)} · {hit.matched_language.toUpperCase()}</small>
                {hit.speaker && <small>{hit.speaker}</small>}
              </div>
              <p>{hit.matched_text}</p>
              {hit.matched_language !== hit.source_language && (
                <small>{copy.original}: {hit.original_text}</small>
              )}
              <button
                type="button"
                className="secondary-button"
                onClick={() => onOpenSession(hit.session_id, hit.matched_text)}
              >
                {copy.showSession}
              </button>
            </li>
          ))}
        </ol>
      )}
      {hasMore && (
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() => void find(matches.length)}
        >
          {copy.more}
        </button>
      )}
    </section>
  );
}
