import { useCallback, useEffect, useState } from "react";
import type { SessionRecord } from "./types";

interface SessionHistoryProps {
  apiUrl: string;
  targetLanguage: string;
  activeSessionId: string | null;
  refreshToken: string;
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function SessionHistory({
  apiUrl,
  targetLanguage,
  activeSessionId,
  refreshToken,
}: SessionHistoryProps) {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const refresh = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/v1/history?limit=20`);
    if (!response.ok) throw new Error("세션 기록을 불러오지 못했습니다.");
    setSessions((await response.json()) as SessionRecord[]);
  }, [apiUrl]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh, refreshToken]);

  function exportSession(session: SessionRecord, format: string) {
    const params = new URLSearchParams({ format });
    if (format !== "json" && targetLanguage) params.set("lang", targetLanguage);
    window.open(
      `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}/export?${params}`,
      "_blank",
    );
  }

  async function remove(session: SessionRecord) {
    setBusyId(session.session_id);
    setError("");
    try {
      const response = await fetch(
        `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "세션 기록을 삭제하지 못했습니다.");
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="history panel">
      <div className="section-heading history-heading">
        <div>
          <span>세션 기록</span>
          <small>최종 자막을 SQLite에 보관하고 표준 자막 파일로 내보냅니다.</small>
        </div>
        <button className="secondary-button" onClick={() => refresh()}>
          새로고침
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}
      <div className="history-list">
        {sessions.length === 0 && <div className="empty">저장된 세션이 없습니다.</div>}
        {sessions.map((session) => {
          const active = activeSessionId === session.session_id;
          return (
            <article className="history-item" key={session.session_id}>
              <div className="history-copy">
                <div className="history-title-row">
                  <strong>{session.title}</strong>
                  {active && <span className="history-live">LIVE</span>}
                </div>
                <small>
                  {dateLabel(session.started_at)} · {session.source_language.toUpperCase()}
                  {" → "}{session.target_languages.map((value) => value.toUpperCase()).join(", ")}
                  {" · "}{session.segment_count} segments
                </small>
                <small>
                  {session.presenter || "발표자 미지정"} · {session.engine}
                  {session.translation_provider !== "none"
                    ? ` / ${session.translation_provider}`
                    : ""}
                </small>
              </div>
              <div className="history-actions">
                {["srt", "vtt", "txt", "json"].map((format) => (
                  <button
                    className="secondary-button"
                    type="button"
                    key={format}
                    onClick={() => exportSession(session, format)}
                  >
                    {format.toUpperCase()}
                  </button>
                ))}
                <button
                  className="danger-quiet"
                  type="button"
                  onClick={() => remove(session)}
                  disabled={active || busyId === session.session_id}
                >
                  삭제
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
