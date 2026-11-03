import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionDetail, TranscriptEvent } from "./types";
import type { Locale } from "./locales";
import { POST_EVENT_COPY } from "./postEventCopy";
import { buildSummaryRequest, type SummaryLanguage, type SummaryProvider } from "./summaryRequestPolicy";
import { summaryCitations } from "./summaryCitations";
import { PostEventTranscripts } from "./PostEventTranscripts";
import { TRANSCRIPT_COPY } from "./transcriptCopy";

interface Recording {
  recording_id: string;
  size_bytes: number;
  duration_seconds: number;
  sha256: string;
  created_at: string;
}

interface Summary {
  transcript_id?: string | null;
  summary_id: string;
  language: string;
  provider: string;
  model: string;
  status: "running" | "completed" | "error";
  content: string;
  error: string | null;
  transcript_sha256: string;
  created_at: string;
  review?: {
    review_id: string;
    content: string;
    approved: boolean;
    created_at: string;
  } | null;
}

interface Archive {
  approved_transcript_id?: string | null;
  recordings: Recording[];
  summary: Summary | null;
  summary_stale: boolean;
}


interface Props {
  apiUrl: string;
  session: SessionDetail;
  locale: Locale;
  segments: TranscriptEvent[];
  onOpenSegment: (segmentId: string) => void;
}

export function PostEventPanel({ apiUrl, session, locale, segments, onOpenSegment }: Props) {
  const copy = POST_EVENT_COPY[locale];
  const [archive, setArchive] = useState<Archive>({
    recordings: [], summary: null, summary_stale: false,
  });
  const [summaryLanguage, setSummaryLanguage] = useState<SummaryLanguage>(locale);
  const [summaryProvider, setSummaryProvider] = useState<SummaryProvider>("ollama");
  const [summaryModel, setSummaryModel] = useState("");
  const [allowExternal, setAllowExternal] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reviewText, setReviewText] = useState("");
  const [summaryTranscriptId, setSummaryTranscriptId] = useState<string | null>(null);
  const [requestedTranscript, setRequestedTranscript] = useState<{ id: string } | null>(null);
  const archiveRequest = useRef(0);
  const root = `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}`;

  useEffect(() => {
    if (summaryTranscriptId && summaryTranscriptId !== archive.approved_transcript_id) {
      setSummaryTranscriptId(null);
      setAllowExternal(false);
    }
  }, [archive.approved_transcript_id, summaryTranscriptId]);

  useEffect(() => {
    setReviewText(archive.summary?.review?.content ?? archive.summary?.content ?? "");
  }, [archive.summary?.summary_id, archive.summary?.review?.review_id]);

  const refresh = useCallback(async () => {
    const requestId = ++archiveRequest.current;
    const response = await fetch(`${root}/archive`);
    if (!response.ok) throw new Error(await response.text());
    const data = (await response.json()) as Archive;
    if (requestId === archiveRequest.current) setArchive(data);
  }, [root]);

  useEffect(() => {
    let mounted = true;
    void refresh()
      .catch(() => { if (mounted) setError(copy.failure); });
    return () => { mounted = false; archiveRequest.current++; };
  }, [refresh, copy.failure]);

  useEffect(() => {
    if (archive.summary?.status !== "running") return;
    const timer = window.setInterval(() => {
      void refresh().catch(() => setError(copy.failure));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [archive.summary?.status, copy.failure, refresh]);

  async function upload() {
    if (!file || busy) return;
    setBusy("upload");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${root}/recordings`, {
        method: "POST",
        headers: { "Content-Type": "audio/wav" },
        body: file,
      });
      if (!response.ok) throw new Error(await response.text());
      setFile(null);
      setMessage(copy.imported);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally {
      setBusy("");
    }
  }

  async function summarize() {
    if (busy || archive.summary?.status === "running") return;
    setBusy("summary");
    setError("");
    try {
      const response = await fetch(`${root}/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildSummaryRequest(
          summaryLanguage, summaryProvider, summaryModel, allowExternal, summaryTranscriptId,
        )),
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally {
      setBusy("");
    }
  }

  function exportSummaryText(format: "txt" | "md") {
    if (archive.summary?.status !== "completed") return;
    const blob = new Blob([archive.summary.review?.content ?? archive.summary.content], {
      type: format === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `langtextflow-${session.session_id}-summary-${archive.summary.language}.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function cancelSummary() {
    if (busy || archive.summary?.status !== "running") return;
    setBusy("cancel-summary"); setError("");
    try {
      const response = await fetch(`${root}/summaries/${encodeURIComponent(archive.summary.summary_id)}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally { setBusy(""); }
  }

  async function saveReview(approved: boolean) {
    const summary = archive.summary;
    if (!summary || busy || summary.review?.approved || archive.summary_stale) return;
    setBusy("review");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${root}/summaries/${encodeURIComponent(summary.summary_id)}/reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: reviewText, approved, expected_review_id: summary.review?.review_id ?? null,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
      setMessage(approved ? copy.approved : copy.reviewSaved);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="history-post-event" aria-label={copy.heading}>
      <h3>{copy.heading}</h3>
      <p>{copy.detail}</p>
      {session.interrupted && <p role="status">{copy.warning}</p>}
      <div className="history-post-actions">
        <button className="secondary-button" type="button" onClick={() => void refresh().catch(() => setError(copy.failure))}>
          {copy.refresh}
        </button>
      </div>
      <h4>{copy.recording}</h4>
      <label className="history-post-upload">
        {copy.importLabel}
        <input
          type="file"
          accept=".wav,audio/wav,audio/x-wav"
          aria-label={copy.selected}
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </label>
      <button
        className="secondary-button"
        type="button"
        disabled={!file || Boolean(busy)}
        onClick={() => void upload()}
      >
        {copy.upload}
      </button>
      {archive.recordings.map((recording) => (
        <div className="history-post-recording" key={recording.recording_id}>
          <small>{Math.round(recording.duration_seconds)}s · {(recording.size_bytes / (1024 * 1024)).toFixed(1)} MiB</small>
          <audio
            controls
            preload="none"
            src={`${root}/recordings/${encodeURIComponent(recording.recording_id)}/audio`}
            aria-label={copy.recording}
          />
          <a className="secondary-button" download
            href={`${root}/recordings/${encodeURIComponent(recording.recording_id)}/audio?download=true`}>
            {copy.downloadRecording}
          </a>
        </div>
      ))}
      <PostEventTranscripts root={root} locale={locale} sourceLanguage={session.source_language} recordings={archive.recordings}
        onChanged={refresh} requestedVersion={requestedTranscript} summary={archive.summary} />
      <h4>{copy.summary}</h4>
      {archive.summary_stale && (
        <p role="alert" className="error-box">{copy.summaryStale}</p>
      )}
      <div className="history-post-actions">
        <label>
          {TRANSCRIPT_COPY[locale].source}
          <select data-testid="summary-transcript-source" value={summaryTranscriptId ?? ""} disabled={Boolean(busy) || archive.summary?.status === "running"}
            onChange={event => { setSummaryTranscriptId(event.target.value || null); setAllowExternal(false); }}>
            <option value="">{TRANSCRIPT_COPY[locale].live}</option>
            {archive.approved_transcript_id && <option value={archive.approved_transcript_id}>
              {TRANSCRIPT_COPY[locale].approvedSource}
            </option>}
          </select>
        </label>
        <label>
          {copy.summaryLanguage}
          <select
            value={summaryLanguage}
            disabled={Boolean(busy) || archive.summary?.status === "running"}
            onChange={(event) => setSummaryLanguage(event.target.value as SummaryLanguage)}
          >
            <option value="ko">{copy.korean}</option>
            <option value="en">{copy.english}</option>
            <option value="ja">{copy.japanese}</option>
          </select>
        </label>
        <label>
          {copy.summaryProvider}
          <select
            value={summaryProvider}
            disabled={Boolean(busy) || archive.summary?.status === "running"}
            onChange={(event) => {
              setSummaryProvider(event.target.value as SummaryProvider);
              setAllowExternal(false);
            }}
          >
            <option value="ollama">{copy.localProvider}</option>
            <option value="openai-compatible">{copy.externalProvider}</option>
          </select>
        </label>
        <label>
          {copy.modelId}
          <input
            type="text"
            maxLength={128}
            value={summaryModel}
            onChange={(event) => setSummaryModel(event.target.value)}
            placeholder={copy.defaultModel}
            disabled={Boolean(busy) || archive.summary?.status === "running"}
          />
        </label>
      </div>
      {summaryProvider === "openai-compatible" && (
        <label>
          <input
            type="checkbox"
            checked={allowExternal}
            disabled={Boolean(busy) || archive.summary?.status === "running"}
            onChange={(event) => setAllowExternal(event.target.checked)}
          />
          {copy.externalConsent}
        </label>
      )}
      <button
        type="button"
        className="secondary-button"
        disabled={Boolean(busy)
          || archive.summary?.status === "running"
          || (summaryProvider === "openai-compatible" && !allowExternal)}
        onClick={() => void summarize()}
      >
        {copy.generate}
      </button>
      {archive.summary?.status === "running" && <>
        <p role="status">{copy.generating}</p>
        <button type="button" className="secondary-button" disabled={Boolean(busy)}
          onClick={() => void cancelSummary()}>{copy.cancelSummary}</button>
      </>}
      {archive.summary?.status === "error" && <p role="alert">{archive.summary.error || copy.failure}</p>}
      {archive.summary?.status === "completed" && (
        <div className="history-post-summary">
          <small>{archive.summary.model} · {archive.summary.language.toUpperCase()}</small>
          <p role="status">{archive.summary.review?.approved ? copy.approved : copy.unreviewed}</p>
          <label>
            {copy.reviewText}
            <textarea
              value={reviewText}
              maxLength={15000}
              rows={10}
              readOnly={Boolean(archive.summary.review?.approved) || archive.summary_stale}
              disabled={Boolean(busy)}
              onChange={(event) => setReviewText(event.target.value)}
            />
          </label>
          <small>{copy.reviewHint}</small>
          {archive.summary.transcript_id && <button type="button" className="secondary-button" data-testid="open-summary-transcript"
            onClick={() => setRequestedTranscript({ id: archive.summary!.transcript_id! })}>
            {TRANSCRIPT_COPY[locale].open}
          </button>}
          <div className="history-post-actions" role="group" aria-label={copy.sourceReferences}>
            {summaryCitations(archive.summary.transcript_id ? "" : reviewText, segments).map(reference => (
              <button key={reference.segmentId} type="button" className="secondary-button"
                data-testid="summary-source-link"
                onClick={() => onOpenSegment(reference.segmentId)}>
                {copy.openSource} {Math.floor(reference.seconds / 60)}:{String(reference.seconds % 60).padStart(2, "0")}
              </button>
            ))}
          </div>
          {!archive.summary.review?.approved && (
            <div className="history-post-actions">
              <button type="button" className="secondary-button"
                disabled={Boolean(busy) || archive.summary_stale || !reviewText.trim()}
                onClick={() => void saveReview(false)}>{copy.saveReview}</button>
              <button type="button" className="secondary-button"
                disabled={Boolean(busy) || archive.summary_stale || !reviewText.trim()}
                onClick={() => void saveReview(true)}>{copy.approveReview}</button>
            </div>
          )}
          <details>
            <summary>{copy.originalDraft}</summary>
            <pre>{archive.summary.content}</pre>
          </details>
          <button type="button" className="secondary-button" onClick={() => exportSummaryText("txt")}>
            {copy.exportSummary}
          </button>
          <button type="button" className="secondary-button" onClick={() => exportSummaryText("md")}>
            {copy.exportMarkdown}
          </button>
        </div>
      )}
      {!archive.summary && <p>{copy.noSummary}</p>}
      {message && <p role="status">{message}</p>}
      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  );
}
