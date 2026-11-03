import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { LIVE_RECORDING_COPY } from "./liveRecordingCopy";

interface Props { apiUrl: string; sessionId: string; locale: Locale }
interface Status {
  session_id: string; session_running: boolean; available: boolean;
  phase: "idle" | "starting" | "recording" | "stopping" | "error";
  active: boolean; error: string | null; start_offset_ms: number | null;
  recording: { recording_id: string } | null;
}

export function LiveRecordingControls(props: Props) {
  return <ScopedControls key={`${props.apiUrl}\n${props.sessionId}`} {...props} />;
}

function ScopedControls({ apiUrl, sessionId, locale }: Props) {
  const t = LIVE_RECORDING_COPY[locale];
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true), epoch = useRef(0), acting = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const statusRequest = useRef<AbortController | null>(null);
  const root = `${apiUrl}/api/v1/session/recording`;

  function parse(value: unknown): Status {
    const s = value as Status;
    if (!s || s.session_id !== sessionId || typeof s.session_running !== "boolean"
      || typeof s.available !== "boolean" || typeof s.active !== "boolean"
      || !["idle", "starting", "recording", "stopping", "error"].includes(s.phase)
      || !(s.error === null || typeof s.error === "string")
      || !(s.start_offset_ms === null || (Number.isFinite(s.start_offset_ms) && s.start_offset_ms >= 0))
      || !(s.recording === null || (s.recording && typeof s.recording.recording_id === "string"
        && /^[a-zA-Z0-9-]{1,36}$/.test(s.recording.recording_id)))) throw new Error(t.failure);
    return s;
  }
  async function refresh(force = false) {
    if (acting.current) return;
    if (statusRequest.current && !force) return;
    const request = ++epoch.current;
    statusRequest.current?.abort();
    const abort = new AbortController(); statusRequest.current = abort;
    try {
      const response = await fetch(root, { signal: abort.signal });
      if (!response.ok) throw new Error(t.failure);
      const data = parse(await response.json());
      if (alive.current && epoch.current === request) { setStatus(data); setError(""); }
    } catch {
      if (alive.current && epoch.current === request) { setStatus(null); setError(t.failure); }
    } finally {
      if (statusRequest.current === abort) statusRequest.current = null;
    }
  }
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 1000);
    return () => { alive.current = false; epoch.current++; controller.current?.abort(); statusRequest.current?.abort(); window.clearInterval(timer); };
  }, [root]);

  async function action(kind: "start" | "stop") {
    if (acting.current || !status?.session_running) return;
    acting.current = true; setBusy(true); setError("");
    const request = ++epoch.current;
    statusRequest.current?.abort(); statusRequest.current = null;
    const abort = new AbortController(); controller.current = abort;
    try {
      const response = await fetch(`${root}/${kind}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_session_id: sessionId }), signal: abort.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : t.failure);
      const parsed = parse(data);
      if (alive.current && epoch.current === request) setStatus(parsed);
    } catch (reason) {
      if (alive.current && epoch.current === request) { setStatus(null); setError(reason instanceof Error ? reason.message : t.failure); }
    } finally {
      acting.current = false;
      if (alive.current && epoch.current === request) { setBusy(false); controller.current = null; }
    }
  }
  const transition = busy || status?.phase === "starting" || status?.phase === "stopping";
  return <section className="history-post-event" data-testid="live-recording-controls" aria-label={t.title}>
    <h3>{t.title}</h3><small>{t.help}</small>
    <p role="status"><strong>{!status || status.phase === "error" ? t.failure : status.phase === "starting" ? t.starting : status.phase === "stopping" ? t.stopping
      : status.active ? t.active : t.idle}</strong></p>
    {status?.start_offset_ms !== null && status?.start_offset_ms !== undefined && <small>{t.offset}: {(status.start_offset_ms / 1000).toFixed(3)}s</small>}
    <div className="history-post-actions">
      <button type="button" className="secondary-button" disabled={transition || !status?.available || status.active}
        onClick={() => void action("start")}>{t.start}</button>
      <button type="button" className="secondary-button" disabled={transition || !status?.active}
        onClick={() => void action("stop")}>{t.stop}</button>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void refresh(true)}>{t.refresh}</button>
    </div>
    {status?.recording && <a className="secondary-button" download href={`${apiUrl}/api/v1/history/${encodeURIComponent(sessionId)}/recordings/${encodeURIComponent(status.recording.recording_id)}/audio?download=true`}>{t.download}</a>}
    {(error || status?.error) && <p role="alert" className="error-box">{error || status?.error}</p>}
  </section>;
}
