import { useCallback, useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { POST_EVENT_COPY } from "./postEventCopy";
import { TRANSCRIPT_COPY } from "./transcriptCopy";
import { summaryCitations } from "./summaryCitations";

interface Segment { segment_id: string; start_ms: number; end_ms: number; text: string }
interface Version {
  transcript_id: string; recording_id: string; model: string; language: string;
  status: "running" | "completed" | "error" | "cancelled"; progress: number;
  error: string | null; created_at: string; provenance: Record<string, unknown>;
  segments?: Segment[];
  review: { review_id: string; approved: boolean; segments?: Segment[] } | null;
}
interface Props {
  root: string; locale: Locale; sourceLanguage: string;
  recordings: { recording_id: string; duration_seconds: number }[];
  onChanged: () => Promise<void>;
  requestedVersion: { id: string } | null;
  summary: { transcript_id?: string | null; content: string; review?: { content: string } | null } | null;
}

export function PostEventTranscripts({ root, locale, sourceLanguage, recordings, onChanged, requestedVersion, summary }: Props) {
  const copy = TRANSCRIPT_COPY[locale];
  const [versions, setVersions] = useState<Version[]>([]);
  const [selected, setSelected] = useState("");
  const [recording, setRecording] = useState("");
  const [model, setModel] = useState("small");
  const [language, setLanguage] = useState<Locale>(() => (["ko", "en", "ja"] as const).find(value => value === sourceLanguage) ?? locale);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const section = useRef<HTMLElement>(null);
  const refresh = useCallback(async () => {
    const id = ++requestId.current;
    const response = await fetch(`${root}/transcripts`);
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json() as { versions: Version[] };
    if (id !== requestId.current) return;
    setVersions(data.versions);
    setSelected(current => current || data.versions[0]?.transcript_id || "");
  }, [root]);
  useEffect(() => {
    void refresh().catch(() => setError(copy.failure));
    return () => { requestId.current++; };
  }, [refresh, copy.failure]);
  useEffect(() => {
    if (!requestedVersion) return;
    setSelected(requestedVersion.id);
    section.current?.scrollIntoView({ block: "start" });
    section.current?.focus({ preventScroll: true });
  }, [requestedVersion]);
  const running = versions.some(version => version.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void refresh().catch(() => setError(copy.failure)), 2500);
    return () => window.clearInterval(timer);
  }, [running, refresh, copy.failure]);

  async function generate() {
    const recordingId = recording || recordings[0]?.recording_id;
    if (!recordingId || busy || running) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${root}/transcripts`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recording_id: recordingId, model, language }),
      });
      if (!response.ok) throw new Error(await response.text());
      const version = await response.json() as Version;
      setSelected(version.transcript_id);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : copy.failure); }
    finally { setBusy(false); }
  }

  return <section ref={section} tabIndex={-1} aria-label={copy.heading} className="history-post-event" data-testid="offline-transcripts">
    <h4>{copy.heading}</h4><p>{copy.detail}</p>
    <div className="history-post-actions">
      <label>{copy.recording}<select value={recording || recordings[0]?.recording_id || ""}
        disabled={busy || running || !recordings.length} onChange={event => setRecording(event.target.value)}>
        {!recordings.length && <option value="">—</option>}
        {recordings.map(item => <option key={item.recording_id} value={item.recording_id}>
          {item.recording_id} · {Math.round(item.duration_seconds)}s
        </option>)}
      </select></label>
      <label>{copy.model}<select value={model} disabled={busy || running} onChange={event => setModel(event.target.value)}>
        {(["tiny", "base", "small"] as const).map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>
      <label>{copy.language}<select value={language} disabled={busy || running} onChange={event => setLanguage(event.target.value as Locale)}>
        {(["ko", "en", "ja"] as const).map(value => <option key={value} value={value}>
          {POST_EVENT_COPY[locale][({ ko: "korean", en: "english", ja: "japanese" } as const)[value]]}
        </option>)}
      </select></label>
      <button type="button" className="secondary-button" disabled={busy || running || !recordings.length}
        onClick={() => void generate()}>{copy.generate}</button>
    </div>
    {versions.length > 0 ? <label>{copy.version}<select value={selected} onChange={event => setSelected(event.target.value)}>
      {versions.map(version => <option key={version.transcript_id} value={version.transcript_id}>
        {version.created_at} · {version.model} · {copy[version.status]} · {version.review?.approved ? copy.approved : copy.draft}
      </option>)}
    </select></label> : <p>{copy.none}</p>}
    {selected && <TranscriptReview key={selected} root={root} id={selected} locale={locale}
      summary={summary?.transcript_id === selected ? summary : null}
      onChanged={async () => { await refresh(); await onChanged(); }} />}
    {error && <p role="alert" className="error-box">{error}</p>}
  </section>;
}

function TranscriptReview({ root, id, locale, onChanged, summary }: {
  root: string; id: string; locale: Locale; onChanged: () => Promise<void>;
  summary: Props["summary"];
}) {
  const copy = TRANSCRIPT_COPY[locale];
  const [version, setVersion] = useState<Version | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const elements = useRef(new Map<string, HTMLTextAreaElement>());
  const url = `${root}/transcripts/${encodeURIComponent(id)}`;
  const load = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json() as Version;
    if (signal?.aborted) return;
    setVersion(data); setSegments(data.review?.segments ?? data.segments ?? []);
  }, [url]);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).catch(() => { if (!controller.signal.aborted) setError(copy.failure); });
    return () => controller.abort();
  }, [load, copy.failure]);
  useEffect(() => {
    if (version?.status !== "running") return;
    const controller = new AbortController();
    const timer = window.setInterval(() => void load(controller.signal).catch(() => {
      if (!controller.signal.aborted) setError(copy.failure);
    }), 2500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [version?.status, load, copy.failure]);

  async function mutate(action: "cancel" | "reviews", approved = false) {
    if (!version || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`${url}/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        ...(action === "reviews" ? { body: JSON.stringify({
          segments, approved, expected_review_id: version.review?.review_id ?? null,
        }) } : {}),
      });
      if (!response.ok) throw new Error(await response.text());
      await load(); await onChanged();
    } catch (reason) { setError(reason instanceof Error ? reason.message : copy.failure); }
    finally { setBusy(false); }
  }
  const locked = Boolean(version?.review?.approved);
  return <div>
    <button type="button" className="secondary-button" disabled={busy}
      onClick={() => void load().catch(() => setError(copy.failure))}>{POST_EVENT_COPY[locale].refresh}</button>
    {version && <p role="status">{copy[version.status]} · {Math.round(version.progress * 100)}% · {locked ? copy.approved : copy.draft}</p>}
    {version?.error && <p role="alert">{version.error}</p>}
    {version?.status === "running" && <button type="button" className="secondary-button" disabled={busy}
      onClick={() => void mutate("cancel")}>{copy.cancel}</button>}
    {version?.status === "completed" && <>
      <div className="history-post-actions" role="group" aria-label={POST_EVENT_COPY[locale].sourceReferences}>
        {summaryCitations(summary?.review?.content ?? summary?.content ?? "", segments.map(segment => ({ ...segment, committed: true }))).map(reference => (
          <button key={reference.segmentId} type="button" className="secondary-button" data-testid="offline-source-link" onClick={() => {
            const element = elements.current.get(reference.segmentId);
            element?.scrollIntoView({ block: "center" }); element?.focus({ preventScroll: true });
          }}>{POST_EVENT_COPY[locale].openSource} {Math.floor(reference.seconds / 60)}:{String(reference.seconds % 60).padStart(2, "0")}</button>
        ))}
      </div>
      <div className="history-transcript-list">
        {segments.map((segment, index) => <label key={segment.segment_id}>
          {copy.review} · {segment.segment_id} · {(segment.start_ms / 1000).toFixed(1)}–{(segment.end_ms / 1000).toFixed(1)}s
          <textarea rows={3} value={segment.text} readOnly={locked} disabled={busy} maxLength={6000} data-testid="offline-transcript-text"
            ref={element => { if (element) elements.current.set(segment.segment_id, element); else elements.current.delete(segment.segment_id); }}
            onChange={event => setSegments(current => current.map((item, i) => i === index ? { ...item, text: event.target.value } : item))} />
        </label>)}
      </div>
      {!locked && <div className="history-post-actions">
        <button type="button" className="secondary-button" disabled={busy || !segments.length} onClick={() => void mutate("reviews")}>{copy.save}</button>
        <button type="button" className="secondary-button" disabled={busy || !segments.length} onClick={() => void mutate("reviews", true)}>{copy.approve}</button>
      </div>}
      <details><summary>{copy.original}</summary><pre>{JSON.stringify({ segments: version.segments, provenance: version.provenance }, null, 2)}</pre></details>
      <a className="secondary-button" href={`${url}/export`} download>{copy.original} · JSON</a>
    </>}
    {error && <p role="alert" className="error-box">{error}</p>}
  </div>;
}
