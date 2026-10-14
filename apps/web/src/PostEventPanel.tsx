import { useCallback, useEffect, useState } from "react";
import type { SessionDetail } from "./types";
import type { Locale } from "./locales";
import { POST_EVENT_COPY } from "./postEventCopy";
import { buildSummaryRequest, type SummaryLanguage, type SummaryProvider } from "./summaryRequestPolicy";

interface Recording {
  recording_id: string;
  size_bytes: number;
  duration_seconds: number;
  sha256: string;
  created_at: string;
}

interface Summary {
  summary_id: string;
  language: string;
  provider: string;
  model: string;
  status: "running" | "completed" | "error";
  content: string;
  error: string | null;
  transcript_sha256: string;
  created_at: string;
}

interface Archive {
  recordings: Recording[];
  summary: Summary | null;
  summary_stale: boolean;
}


interface Props {
  apiUrl: string;
  session: SessionDetail;
  locale: Locale;
}

export function PostEventPanel({ apiUrl, session, locale }: Props) {
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
  const root = `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}`;

  const refresh = useCallback(async () => {
    const response = await fetch(`${root}/archive`);
    if (!response.ok) throw new Error(await response.text());
    const data = (await response.json()) as Archive;
    setArchive(data);
  }, [root]);

  useEffect(() => {
    let mounted = true;
    fetch(`${root}/archive`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json() as Promise<Archive>;
      })
      .then((data) => { if (mounted) setArchive(data); })
      .catch(() => { if (mounted) setError(copy.failure); });
    return () => { mounted = false; };
  }, [root, copy.failure]);

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
          summaryLanguage, summaryProvider, summaryModel, allowExternal,
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

  function exportSummaryText() {
    if (archive.summary?.status !== "completed") return;
    const blob = new Blob([archive.summary.content], {
      type: "text/plain;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `langtextflow-${session.session_id}-summary-${archive.summary.language}.txt`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
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
        </div>
      ))}
      <h4>{copy.summary}</h4>
      {archive.summary_stale && (
        <p role="alert" className="error-box">{copy.summaryStale}</p>
      )}
      <div className="history-post-actions">
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
      {archive.summary?.status === "running" && <p role="status">{copy.generating}</p>}
      {archive.summary?.status === "error" && <p role="alert">{archive.summary.error || copy.failure}</p>}
      {archive.summary?.status === "completed" && (
        <div className="history-post-summary">
          <small>{archive.summary.model} · {archive.summary.language.toUpperCase()}</small>
          <pre>{archive.summary.content}</pre>
          <button type="button" className="secondary-button" onClick={exportSummaryText}>
            {copy.exportSummary}
          </button>
        </div>
      )}
      {!archive.summary && <p>{copy.noSummary}</p>}
      {message && <p role="status">{message}</p>}
      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  );
}
