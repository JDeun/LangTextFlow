import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./history.css";
import { HISTORY_COPY } from "./historyCopy";
import { useI18n } from "./i18n";
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
  const { locale } = useI18n();
  const copy = HISTORY_COPY[locale];
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
  const refreshRequestRef = useRef(0);
  const detailRequestRef = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = ++refreshRequestRef.current;
    const response = await fetch(`${apiUrl}/api/v1/history?limit=50`);
    if (!response.ok) throw new Error(copy.loadFailed);
    const nextSessions = (await response.json()) as SessionRecord[];
    if (requestId === refreshRequestRef.current) setSessions(nextSessions);
  }, [apiUrl, copy.loadFailed]);

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
      session.interrupted ? `interrupted ${copy.recovered}` : "",
      ...session.target_languages,
    ].join("\n").toLocaleLowerCase().includes(query));
  }, [copy.recovered, sessionQuery, sessions]);

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
    const requestId = ++detailRequestRef.current;
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
      if (requestId !== detailRequestRef.current) return;
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
      if (requestId === detailRequestRef.current) {
        setError(reason instanceof Error ? reason.message : copy.detailFailed);
      }
    } finally {
      if (requestId === detailRequestRef.current) setDetailBusy(false);
    }
  }

  function closeDetail() {
    detailRequestRef.current += 1;
    setDetailBusy(false);
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
      setError(copy.titleRequired);
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
      setError(reason instanceof Error ? reason.message : copy.saveFailed);
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
      setError(reason instanceof Error ? reason.message : copy.deleteFailed);
    } finally {
      setBusyId("");
    }
  }

  return (
    <section className="history panel">
      <div className="section-heading history-heading">
        <div>
          <span>{copy.heading}</span>
          <small>{copy.headingHelp}</small>
        </div>
        <button
          className="secondary-button"
          onClick={() => void refresh().catch((reason: Error) => setError(reason.message))}
        >
          {copy.refresh}
        </button>
      </div>

      <div className="history-search-row">
        <input
          value={sessionQuery}
          onChange={(event) => setSessionQuery(event.target.value)}
          placeholder={copy.searchPlaceholder}
        />
        <span>{filteredSessions.length} / {sessions.length}</span>
      </div>

      {error && <div className="error-box">{error}</div>}
      <div className="history-list">
        {filteredSessions.length === 0 && (
          <div className="empty">{sessions.length ? copy.noMatch : copy.noSessions}</div>
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
                  {session.interrupted && <span className="history-interrupted">INTERRUPTED</span>}
                </div>
                <small>
                  {dateLabel(session.started_at)} · {session.source_language.toUpperCase()}
                  {" → "}{session.target_languages.map((value) => value.toUpperCase()).join(", ")}
                  {" · "}{session.segment_count} segments
                </small>
                <small>
                  {session.presenter || copy.presenterMissing} · {session.engine}
                  {session.translation_provider !== "none" ? ` / ${session.translation_provider}` : ""}
                  {session.interrupted ? ` · ${copy.recovered}` : ""}
                </small>
                {session.notes && <small className="history-note-preview">{session.notes}</small>}
              </div>
              <div className="history-actions">
                <button className="secondary-button history-open" type="button" onClick={() => openDetail(session)} disabled={detailBusy}>
                  {opened ? copy.reopen : copy.details}
                </button>
                {["srt", "vtt", "txt", "json"].map((format) => (
                  <button className="secondary-button" type="button" key={format} onClick={() => exportSession(session, format)}>
                    {format.toUpperCase()}
                  </button>
                ))}
                <button className="danger-quiet" type="button" onClick={() => remove(session)} disabled={active || busyId === session.session_id}>
                  {copy.delete}
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
              <span className="eyebrow">{copy.detail}</span>
              <h3>{selected.title}</h3>
              <small>
                {dateLabel(selected.started_at)}
                {selected.ended_at ? ` → ${dateLabel(selected.ended_at)}` : ` · ${copy.running}`}
                {selected.interrupted ? ` · ${copy.recoveredShort}` : ""}
                {" · "}{selected.segment_count} segments
              </small>
            </div>
            <button className="secondary-button" type="button" onClick={closeDetail}>{copy.close}</button>
          </div>

          <div className="history-detail-meta">
            <span>{copy.presenter} <strong>{selected.presenter || copy.unspecified}</strong></span>
            <span>Preset <strong>{selected.preset}</strong></span>
            <span>ASR <strong>{selected.engine}</strong></span>
            <span>{copy.translation} <strong>{selected.translation_provider === "none" ? copy.disabled : selected.translation_provider}</strong></span>
            {selected.interrupted && <span>{copy.endStatus} <strong>{copy.autoRecovered}</strong></span>}
          </div>

          <div className="history-metadata-editor">
            <label>
              {copy.recordTitle}
              <input value={editTitle} maxLength={120} onChange={(event) => setEditTitle(event.target.value)} disabled={selectedIsActive || saveBusy} />
            </label>
            <label>
              {copy.notes}
              <textarea value={editNotes} maxLength={8000} rows={4} onChange={(event) => setEditNotes(event.target.value)} disabled={selectedIsActive || saveBusy} placeholder={copy.notesPlaceholder} />
            </label>
            <div className="history-metadata-actions">
              <small>{copy.contextImmutable}{selectedIsActive ? copy.activeEditHint : ""}</small>
              <button className="secondary-button" type="button" onClick={saveMetadata} disabled={selectedIsActive || saveBusy || !editTitle.trim()}>
                {saveBusy ? copy.saving : copy.saveMetadata}
              </button>
            </div>
          </div>

          {(selected.context.description || selected.context.hotwords.length > 0 || selected.context.reference_documents.length > 0) && (
            <div className="history-context">
              {selected.context.description && <p>{selected.context.description}</p>}
              {selected.context.hotwords.length > 0 && <small>Hotwords: {selected.context.hotwords.join(", ")}</small>}
              {selected.context.reference_documents.length > 0 && (
                <small>Reference documents: {selected.context.reference_documents.map((document) => document.filename).join(", ")}</small>
              )}
            </div>
          )}

          <div className="history-detail-controls">
            <label>
              {copy.displayLanguage}
              <select value={detailLanguage} onChange={(event) => setDetailLanguage(event.target.value)}>
                {detailLanguages.map((language) => (
                  <option value={language} key={language}>
                    {language.toUpperCase()}{language === selected.source_language ? ` · ${copy.source}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="history-transcript-search">
              {copy.transcriptSearch}
              <input value={transcriptQuery} onChange={(event) => setTranscriptQuery(event.target.value)} placeholder={copy.transcriptPlaceholder} />
            </label>
            <div className="history-match-count">{filteredSegments.length} / {segments.length} segments</div>
          </div>

          <div className="history-detail-export">
            {["srt", "vtt", "txt", "json"].map((format) => (
              <button className="secondary-button" type="button" key={format} onClick={() => exportSession(selected, format, detailLanguage)}>
                {format === "json" ? copy.allJson : `${detailLanguage.toUpperCase()} ${format.toUpperCase()}`}
              </button>
            ))}
          </div>

          <div className="history-transcript-list">
            {filteredSegments.length === 0 && <div className="empty">{segments.length ? copy.noCaptionMatch : copy.noCaptions}</div>}
            {filteredSegments.map((segment) => (
              <article className="history-transcript-segment" key={segment.segment_id}>
                <div className="history-transcript-time">
                  <span>{timecode(segment.start_ms)}</span>
                  {segment.end_ms !== null && <span>– {timecode(segment.end_ms)}</span>}
                </div>
                <div className="history-transcript-copy">
                  {segment.speaker && <small className="history-speaker">{segment.speaker}</small>}
                  <div>{segmentText(segment, detailLanguage)}</div>
                  {detailLanguage !== segment.source_language && segment.translations[detailLanguage] && <small className="history-source-text">{segment.text}</small>}
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
