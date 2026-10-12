import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";

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

const COPY: Record<Locale, {
  heading: string; helper: string; placeholder: string; search: string;
  more: string; noResults: string; error: string; showSession: string;
  original: string;
}> = {
  ko: {
    heading: "행사 전체 전사 검색",
    helper: "이 행사에 연결된 세션의 확정 원문·번역문에서 검색합니다.",
    placeholder: "키워드 또는 발언 내용",
    search: "검색", more: "결과 더 보기", noResults: "검색 결과가 없습니다.",
    error: "전사 검색에 실패했습니다.", showSession: "세션에서 확인",
    original: "원문",
  },
  en: {
    heading: "Search event transcripts",
    helper: "Find committed source and translated captions across this event's sessions.",
    placeholder: "Keyword or phrase",
    search: "Search", more: "More results", noResults: "No matching transcript segments.",
    error: "Transcript search failed.", showSession: "Open source session",
    original: "Source",
  },
  ja: {
    heading: "イベント全文検索",
    helper: "このイベントの確定した原文・翻訳テキストを横断検索します。",
    placeholder: "キーワードまたは発言",
    search: "検索", more: "さらに表示", noResults: "該当する発言がありません。",
    error: "全文検索に失敗しました。", showSession: "セッションを開く",
    original: "原文",
  },
};

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
  const copy = COPY[locale];
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
            setQuery(event.target.value);
            setSubmitted(false);
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
