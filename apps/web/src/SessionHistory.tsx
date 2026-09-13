import { useCallback, useEffect, useMemo, useState } from "react";
import "./history.css";
import type { SessionDetail, SessionRecord, TranscriptEvent } from "./types";

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

function timecode(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const hours = Math.floor(totalSeconds / 3600);
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function sessionLanguages(session: SessionRecord) {
  return [
    session.source_language,
    ...session.target_languages.filter((language) => language !== session.source_language),
  ];
}

function segmentText(segment: TranscriptEvent, language: string) {
  if (language === segment.source_language) return segment.text;
  return segment.translations[language] || segment.text;
}

function matchesTranscript(segment: TranscriptEvent, query: string) {
  if (!query) return true;
  const normalized = query.toLocaleLowerCase();
  return [segment.text, segment.speaker || "", ...Object.values(segment.translations)]
    .join("\n")
    .toLocaleLowerCase()
    .includes(normalized);
}

export function SessionHistory({
  apiUrl,
  targetLanguage,
  activeSessionId,
  refreshToken,
}: SessionHistoryProps) {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [sessionQuery, setSessionQuery] = useState("");
  const [selected, setSelected] = useState<SessionDetail | null>(null);
  const [segments, setSegments] = useState<TranscriptEvent[]>([]);
  const [transcriptQuery, setTranscriptQuery] = useState("");
  const [detailLanguage, setDetailLanguage] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [detailBusy, setDetailBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);

  const refresh = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/v1/history?limit=50`);
    if (!response.ok) throw new Error("세션 기록을 불러오지 못했습니다.");
    setSessions((await response.json()) as SessionRecord[]);
  }, [apiUrl]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh, refreshToken]);

  const filteredSessions = useMemo(() => {
    const query = sessionQuery.trim().toLocaleLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) => [
      session.title,
      session.notes,
      session.presenter || "",
      session.engine,
      session.translation_provider,
      session.preset,
      session.source_language,
      session.interrupted ? "interrupted 비정상 종료 복구" : "",
      ...session.target_languages,
    ].join("\n").toLocaleLowerCase().includes(query));
  }, [sessionQuery, sessions]);

  const filteredSegments = useMemo(() => {
    const query = transcriptQuery.trim();
    return segments.filter((segment) => matchesTranscript(segment, query));
  }, [segments, transcriptQuery]);

  const detailLanguages = selected ? sessionLanguages(selected) : [];
  const selectedIsActive = selected?.session_id === activeSessionId;

  function exportSession(session: SessionRecord, format: string, language = targetLanguage) {
    const params = new URLSearchParams({ format });
    if (format !== "json" && language) params.set("lang", language);
    window.open(
      `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}/export?${params}`,
      "_blank",
    );
  }

  async function openDetail(session: SessionRecord) {
    setDetailBusy(true);
    setError("");
    setTranscriptQuery("");
    try {
      const sessionId = encodeURIComponent(session.session_id);
      const [detailResponse, captionsResponse] = await Promise.all([
        fetch(`${apiUrl}/api/v1/history/${sessionId}`),
        fetch(`${apiUrl}/api/v1/history/${sessionId}/captions`),
      ]);
      if (!detailResponse.ok) throw new Error(await detailResponse.text());
      if (!captionsResponse.ok) throw new Error(await captionsResponse.text());
      const detail = (await detailResponse.json()) as SessionDetail;
      const transcript = (await captionsResponse.json()) as TranscriptEvent[];
      const languages = sessionLanguages(detail);
      const preferred = languages.includes(targetLanguage)
        ? targetLanguage
        : detail.target_languages[0] || detail.source_language;
      setSelected(detail);
      setSegments(transcript);
      setDetailLanguage(preferred);
      setEditTitle(detail.title);
      setEditNotes(detail.notes || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "세션 상세 기록을 불러오지 못했습니다.");
    } finally {
      setDetailBusy(false);
    }
  }

  function closeDetail() {
    setSelected(null);
    setSegments([]);
    setTranscriptQuery("");
    setDetailLanguage("");
    setEditTitle("");
    setEditNotes("");
  }

  async function saveMetadata() {
    if (!selected) return;
    const title = editTitle.trim();
    if (!title) {
      setError("세션 제목은 비워둘 수 없습니다.");
      return;
    }
    setSaveBusy(true);
    setError("");
    try {
      const response = await fetch(
        `${apiUrl}/api/v1/history/${encodeURIComponent(selected.session_id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, notes: editNotes }),
        },
      );
      if (!response.ok) throw new Error(await response.text());
      const updated = (await response.json()) as SessionDetail;
      setSelected(updated);
      setEditTitle(updated.title);
      setEditNotes(updated.notes || "");
      setSessions((current) => current.map((session) => (
        session.session_id === updated.session_id
          ? { ...session, title: updated.title, notes: updated.notes }
          : session
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "세션 메타데이터를 저장하지 못했습니다.");
    } finally {
      setSaveBusy(false);
    }
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
      if (selected?.session_id === session.session_id) closeDetail();
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
          <small>세션을 열어 제목·메모를 정리하고 전체 원문/번역 transcript를 검색할 수 있습니다.</small>
        </div>
        <button className="secondary-button" onClick={() => refresh()}>
          새로고침
        </button>
      </div>

      <div className="history-search-row">
        <input
          value={sessionQuery}
          onChange={(event) => setSessionQuery(event.target.value)}
          placeholder="세션 제목, 메모, 발표자, 엔진, 언어 검색"
        />
        <span>{filteredSessions.length} / {sessions.length}</span>
      </div>

      {error && <div className="error-box">{error}</div>}
      <div className="history-list">
        {filteredSessions.length === 0 && (
          <div className="empty">
            {sessions.length ? "검색 조건과 일치하는 세션이 없습니다." : "저장된 세션이 없습니다."}
          </div>
        )}
        {filteredSessions.map((session) => {
          const active = activeSessionId === session.session_id;
          const opened = selected?.session_id === session.session_id;
          return (
            <article className={`history-item ${opened ? "selected" : ""}`} key={session.session_id}>
              <div className="history-copy">
                <div className="history-title-row">
                  <strong>{session.title}</strong>
                  {active && <span className="history-live">LIVE</span>}
                  {session.interrupted && (
                    <span className="history-interrupted">INTERRUPTED</span>
                  )}
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
                  {session.interrupted ? " · 비정상 종료 후 복구됨" : ""}
                </small>
                {session.notes && <small className="history-note-preview">{session.notes}</small>}
              </div>
              <div className="history-actions">
                <button
                  className="secondary-button history-open"
                  type="button"
                  onClick={() => openDetail(session)}
                  disabled={detailBusy}
                >
                  {opened ? "다시 열기" : "상세 / 검색"}
                </button>
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

      {selected && (
        <section className="history-detail">
          <div className="history-detail-header">
            <div>
              <span className="eyebrow">Session Detail</span>
              <h3>{selected.title}</h3>
              <small>
                {dateLabel(selected.started_at)}
                {selected.ended_at ? ` → ${dateLabel(selected.ended_at)}` : " · 진행 중"}
                {selected.interrupted ? " · 비정상 종료 복구" : ""}
                {" · "}{selected.segment_count} segments
              </small>
            </div>
            <button className="secondary-button" type="button" onClick={closeDetail}>
              닫기
            </button>
          </div>

          <div className="history-detail-meta">
            <span>발표자 <strong>{selected.presenter || "미지정"}</strong></span>
            <span>Preset <strong>{selected.preset}</strong></span>
            <span>ASR <strong>{selected.engine}</strong></span>
            <span>
              번역 <strong>{selected.translation_provider === "none" ? "사용 안 함" : selected.translation_provider}</strong>
            </span>
            {selected.interrupted && (
              <span>종료 상태 <strong>비정상 종료 후 자동 복구</strong></span>
            )}
          </div>

          <div className="history-metadata-editor">
            <label>
              기록용 제목
              <input
                value={editTitle}
                maxLength={120}
                onChange={(event) => setEditTitle(event.target.value)}
                disabled={selectedIsActive || saveBusy}
              />
            </label>
            <label>
              운영 메모
              <textarea
                value={editNotes}
                maxLength={8000}
                rows={4}
                onChange={(event) => setEditNotes(event.target.value)}
                disabled={selectedIsActive || saveBusy}
                placeholder="후속 작업, 품질 이슈, 행사 정보 등을 기록하세요."
              />
            </label>
            <div className="history-metadata-actions">
              <small>
                라이브 당시 모델 입력인 Session Context는 수정하지 않습니다.
                {selectedIsActive ? " 현재 진행 중인 세션은 종료 후 편집할 수 있습니다." : ""}
              </small>
              <button
                className="secondary-button"
                type="button"
                onClick={saveMetadata}
                disabled={selectedIsActive || saveBusy || !editTitle.trim()}
              >
                {saveBusy ? "저장 중…" : "제목 / 메모 저장"}
              </button>
            </div>
          </div>

          {(selected.context.description
            || selected.context.hotwords.length > 0
            || selected.context.reference_documents.length > 0) && (
            <div className="history-context">
              {selected.context.description && <p>{selected.context.description}</p>}
              {selected.context.hotwords.length > 0 && (
                <small>Hotwords: {selected.context.hotwords.join(", ")}</small>
              )}
              {selected.context.reference_documents.length > 0 && (
                <small>
                  Reference documents: {selected.context.reference_documents
                    .map((document) => document.filename)
                    .join(", ")}
                </small>
              )}
            </div>
          )}

          <div className="history-detail-controls">
            <label>
              표시 언어
              <select
                value={detailLanguage}
                onChange={(event) => setDetailLanguage(event.target.value)}
              >
                {detailLanguages.map((language) => (
                  <option value={language} key={language}>
                    {language.toUpperCase()}{language === selected.source_language ? " · 원문" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="history-transcript-search">
              Transcript 검색
              <input
                value={transcriptQuery}
                onChange={(event) => setTranscriptQuery(event.target.value)}
                placeholder="원문·모든 번역·speaker에서 검색"
              />
            </label>
            <div className="history-match-count">
              {filteredSegments.length} / {segments.length} segments
            </div>
          </div>

          <div className="history-detail-export">
            {["srt", "vtt", "txt", "json"].map((format) => (
              <button
                className="secondary-button"
                type="button"
                key={format}
                onClick={() => exportSession(selected, format, detailLanguage)}
              >
                {format === "json" ? "전체 JSON" : `${detailLanguage.toUpperCase()} ${format.toUpperCase()}`}
              </button>
            ))}
          </div>

          <div className="history-transcript-list">
            {filteredSegments.length === 0 && (
              <div className="empty">
                {segments.length ? "검색어와 일치하는 자막이 없습니다." : "저장된 자막이 없습니다."}
              </div>
            )}
            {filteredSegments.map((segment) => (
              <article className="history-transcript-segment" key={segment.segment_id}>
                <div className="history-transcript-time">
                  <span>{timecode(segment.start_ms)}</span>
                  {segment.end_ms !== null && <span>– {timecode(segment.end_ms)}</span>}
                </div>
                <div className="history-transcript-copy">
                  {segment.speaker && <small className="history-speaker">{segment.speaker}</small>}
                  <div>{segmentText(segment, detailLanguage)}</div>
                  {detailLanguage !== segment.source_language && segment.translations[detailLanguage] && (
                    <small className="history-source-text">{segment.text}</small>
                  )}
                </div>
                <div className="history-transcript-state">
                  <span>{segment.stage}</span>
                  <small>v{segment.version}</small>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
