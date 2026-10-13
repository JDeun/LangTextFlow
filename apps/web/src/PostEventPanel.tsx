import { useCallback, useEffect, useState } from "react";
import type { SessionDetail } from "./types";
import type { Locale } from "./locales";
import { POST_EVENT_COPY } from "./postEventCopy";

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
}


interface Props {
  apiUrl: string;
  session: SessionDetail;
  locale: Locale;
}

export function PostEventPanel({ apiUrl, session, locale }: Props) {
  const copy = POST_EVENT_COPY[locale];
  const [archive, setArchive] = useState<Archive>({ recordings: [], summary: null });
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
        body: JSON.stringify({ language: locale, provider: "ollama" }),
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
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
        </div>
      ))}
      <h4>{copy.summary}</h4>
      <button
        type="button"
        className="secondary-button"
        disabled={Boolean(busy) || archive.summary?.status === "running"}
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
        </div>
      )}
      {!archive.summary && <p>{copy.noSummary}</p>}
      {message && <p role="status">{message}</p>}
      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  );
}
