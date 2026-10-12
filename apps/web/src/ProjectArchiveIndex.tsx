import { useEffect, useState } from "react";
import type { Locale } from "./locales";

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

const LABELS: Record<Locale, {
  title: string; sub: string; loading: string; retry: string;
  none: string; transcript: string; recording: string; summary: string;
  stale: string; current: string; absent: string; running: string;
  page: string; previous: string; next: string; export: string;
  open: string; failure: string; interrupted: string; report: string;
}> = {
  ko: {
    title: "행사 아카이브 목록", sub: "세션별 녹음·요약 현황과 전사문 최신성을 확인합니다.",
    loading: "아카이브 불러오는 중", retry: "다시 불러오기",
    none: "이 행사에 연결된 세션이 없습니다.", transcript: "전사 구간", recording: "녹음",
    summary: "요약", stale: "전사 변경 — 요약 재검토 필요", current: "요약 생성 당시 전사에서 변경 없음 · 내용 검수 필요",
    absent: "없음", running: "생성 중", page: "세션",
    previous: "이전", next: "다음", export: "현재 페이지 JSON 저장",
    open: "세션 열기", failure: "아카이브 정보를 불러오지 못했습니다.",
    interrupted: "중단된 세션", report: "현재 페이지 근거 보고서(Markdown)",
  },
  en: {
    title: "Event archive index", sub: "Review recordings, summaries and transcript freshness for each session.",
    loading: "Loading event archive", retry: "Retry",
    none: "No sessions are linked to this event.", transcript: "Segments", recording: "Recordings",
    summary: "Summary", stale: "Transcript changed — review summary", current: "Source revision unchanged · summary not verified",
    absent: "None", running: "Generating", page: "Sessions",
    previous: "Previous", next: "Next", export: "Export current page as JSON",
    open: "Open session", failure: "Could not load event archive.",
    interrupted: "Interrupted session", report: "Download cited report (Markdown)",
  },
  ja: {
    title: "イベントアーカイブ一覧", sub: "セッションごとの録音・要約・原文の更新状態を確認します。",
    loading: "アーカイブを読み込み中", retry: "再読み込み",
    none: "このイベントにセッションはありません。", transcript: "文字起こし", recording: "録音",
    summary: "要約", stale: "原文変更 — 要約の再確認が必要", current: "原文の版は変更なし・内容確認が必要",
    absent: "なし", running: "生成中", page: "セッション",
    previous: "前へ", next: "次へ", export: "表示ページをJSON保存",
    open: "セッションを開く", failure: "アーカイブを読み込めませんでした。",
    interrupted: "中断セッション", report: "表示ページの根拠レポート(Markdown)",
  },
};

const PAGE_SIZE = 25;

export function ProjectArchiveIndex({ apiUrl, projectId, locale, onOpenSession }: Props) {
  const t = LABELS[locale];
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
