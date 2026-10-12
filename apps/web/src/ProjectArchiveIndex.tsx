import { useEffect, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_ARCHIVE_COPY } from "./projectArchiveCopy";

interface RecordingRef {
  recording_id: string;
  sha256: string;
  duration_seconds: number;
  size_bytes: number;
}

interface SummaryRef {
  summary_id: string;
  language: string;
  status: "running" | "completed" | "error";
  model: string;
  provider: string;
  transcript_sha256: string;
  prompt_version: string;
  stale: boolean;
  excerpt: string;
  created_at: string;
}

interface SessionArchiveIndex {
  session_id: string;
  title: string;
  started_at: string;
  ended_at: string | null;
  interrupted: boolean;
  source_language: string;
  target_languages: string[];
  segment_count: number;
  recording_count: number;
  recording_duration_seconds: number;
  recordings: RecordingRef[];
  summary: SummaryRef | null;
}

interface Manifest {
  schema_version: number;
  manifest_kind: string;
  generated_at: string;
  project: { project_id: string; title: string };
  pagination: { limit: number; offset: number; total_sessions_at_query: number; has_more: boolean };
  sessions: SessionArchiveIndex[];
  limitations: string;
}

interface Props {
  apiUrl: string;
  projectId: string;
  locale: Locale;
  onOpenSession: (sessionId: string) => void;
}


const PAGE_SIZE = 25;

export function ProjectArchiveIndex({ apiUrl, projectId, locale, onOpenSession }: Props) {
  const t = PROJECT_ARCHIVE_COPY[locale];
  const [page, setPage] = useState(0);
  const [snapshot, setSnapshot] = useState<Manifest | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    setPage(0);
    setSnapshot(null);
    setError("");
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(page * PAGE_SIZE),
    });
    setLoading(true);
    setError("");
    fetch(
      `${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/archive?${params}`,
      { signal: controller.signal },
    ).then(async response => {
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()) as Manifest;
    }).then(data => {
      if (!controller.signal.aborted) setSnapshot(data);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : t.failure);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [apiUrl, projectId, page, refresh, t.failure]);

  function exportJson() {
    if (!snapshot) return;
    const file = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = href;
    link.download = `event-archive-${projectId}-page-${page + 1}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(href);
  }

  return (
    <section className="project-archive-index" aria-label={t.title}>
      <div className="project-archive-header">
        <div><h3>{t.title}</h3><small>{t.sub}</small></div>
        <div className="event-project-buttons">
          <button type="button" className="secondary-button" onClick={() => setRefresh(value => value + 1)}>{t.retry}</button>
          <button type="button" className="secondary-button" disabled={!snapshot || loading} onClick={exportJson}>{t.export}</button>
          {snapshot && !loading && (
            <a
              className="secondary-button"
              href={`${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/report?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`}
              download="event-project-report.md"
            >
              {t.report}
            </a>
          )}
        </div>
      </div>
      {loading && <p role="status">{t.loading}…</p>}
      {error && <p role="alert" className="error-box">{error}</p>}
      {snapshot && !loading && (
        <>
          <small>
            {t.page}: {snapshot.pagination.total_sessions_at_query === 0 ? 0 : snapshot.pagination.offset + 1}
            – {snapshot.pagination.offset + snapshot.sessions.length} / {snapshot.pagination.total_sessions_at_query}
          </small>
          {snapshot.sessions.length === 0 && <p>{t.none}</p>}
          <ul className="project-archive-list">
            {snapshot.sessions.map(session => (
              <li key={session.session_id}>
                <div>
                  <strong>{session.title}</strong>
                  {session.interrupted && <small> · {t.interrupted}</small>}
                  <small> · {t.transcript}: {session.segment_count} · {t.recording}: {session.recording_count}</small>
                </div>
                <div className="project-archive-status">
                  {t.summary}: {session.summary
                    ? session.summary.status === "running"
                      ? t.running
                      : session.summary.stale
                        ? t.stale
                        : session.summary.status === "completed" ? t.current : session.summary.status
                    : t.absent}
                </div>
                <button type="button" className="secondary-button" onClick={() => onOpenSession(session.session_id)}>
                  {t.open}
                </button>
              </li>
            ))}
          </ul>
          <div className="event-project-buttons">
            <button type="button" className="secondary-button" disabled={page === 0 || loading} onClick={() => setPage(n => n - 1)}>{t.previous}</button>
            <button type="button" className="secondary-button" disabled={!snapshot.pagination.has_more || loading} onClick={() => setPage(n => n + 1)}>{t.next}</button>
          </div>
        </>
      )}
    </section>
  );
}
