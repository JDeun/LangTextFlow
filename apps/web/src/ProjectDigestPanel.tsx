import { useCallback, useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_DIGEST_COPY } from "./projectDigestCopy";
import { POST_EVENT_COPY } from "./postEventCopy";
import { buildSummaryRequest, parseSummaryExternalProviders, type SummaryLanguage, type SummaryProvider } from "./summaryRequestPolicy";

interface Version {
  digest_id: string; status: "running" | "completed" | "error";
  language: string; provider: string; model: string; prompt_version: string;
  input_sha256: string; created_at: string; stale: boolean; error: string | null;
  review: { review_id: string; approved: boolean; content?: string } | null;
}
interface Listing {
  versions: Version[];
  input: { input_sha256: string; session_count: number; excluded_running_count: number; interrupted_count: number } | null;
  input_error: string | null; summary_external_providers: unknown;
}
interface Detail extends Version {
  content: string; evidence_available: boolean;
  snapshot: {
    source: string;
    sessions: { session_id: string; title: string; started_at: string; transcript_id: string | null }[];
    citations: { alias: string; marker: string; session_id: string; segment_id: string; start_ms: number; transcript_id: string | null }[];
  } | null;
}
interface Props {
  apiUrl: string; projectId: string; locale: Locale;
  onOpenSession: (sessionId: string) => void;
}

// A new endpoint/project scope gets new state immediately, before effects run.
export function ProjectDigestPanel(props: Props) {
  return <ScopedProjectDigestPanel key={`${props.apiUrl}\n${props.projectId}`} {...props} />;
}

function ScopedProjectDigestPanel({ apiUrl, projectId, locale, onOpenSession }: Props) {
  const t = PROJECT_DIGEST_COPY[locale];
  const c = POST_EVENT_COPY[locale];
  const root = `${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/digests`;
  const [listing, setListing] = useState<Listing | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [policy, setPolicy] = useState<SummaryProvider[] | null>(null);
  const [language, setLanguage] = useState<SummaryLanguage>(locale);
  const [provider, setProvider] = useState<SummaryProvider>("ollama");
  const [model, setModel] = useState("");
  const [consent, setConsent] = useState(false);
  const [reviewText, setReviewText] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const epoch = useRef(0);
  const selected = useRef<string | null>(null);
  const needsConsent = policy?.includes(provider) ?? false;
  const running = listing?.versions.some(version => version.status === "running") ?? false;

  const read = useCallback(async (url: string, init?: RequestInit) => {
    const response = await fetch(url, init);
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(typeof data?.detail === "string" ? data.detail : c.failure);
    }
    return await response.json();
  }, [c.failure]);

  const refresh = useCallback(async (preferred?: string) => {
    const request = ++epoch.current;
    const current = () => alive.current && epoch.current === request;
    setPolicy(null); setConsent(false); setLoading(true);
    try {
      const data = await read(root) as Listing;
      if (!current()) return;
      if (!data || !Array.isArray(data.versions) || !("input" in data)
        || data.versions.some(version => !version || typeof version.digest_id !== "string"
          || typeof version.created_at !== "string" || typeof version.language !== "string"
          || !["running", "completed", "error"].includes(version.status))
        || (data.input !== null && (!data.input || typeof data.input.input_sha256 !== "string"
          || !Number.isInteger(data.input.session_count) || !Number.isInteger(data.input.excluded_running_count)))) throw new Error(c.failure);
      setListing(data);
      setPolicy(parseSummaryExternalProviders(data.summary_external_providers));
      const id = preferred || selected.current || data.versions[0]?.digest_id;
      const valid = data.versions.find(version => version.digest_id === id)?.digest_id ?? data.versions[0]?.digest_id;
      selected.current = valid ?? null;
      if (!valid) { setDetail(null); return; }
      const record = await read(`${root}/${encodeURIComponent(valid)}`) as Detail;
      if (!current()) return;
      if (!record || record.digest_id !== valid || typeof record.evidence_available !== "boolean"
        || typeof record.content !== "string" || typeof record.stale !== "boolean"
        || (record.evidence_available && (!record.snapshot || typeof record.snapshot.source !== "string"
          || !Array.isArray(record.snapshot.sessions) || !Array.isArray(record.snapshot.citations)))) throw new Error(c.failure);
      setDetail(record);
    } catch (reason) {
      if (current()) { setPolicy(null); setDetail(null); setError(reason instanceof Error ? reason.message : c.failure); }
    } finally { if (current()) setLoading(false); }
  }, [c.failure, read, root]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => { alive.current = false; epoch.current++; };
  }, [refresh]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(timer);
  }, [refresh, running]);
  useEffect(() => {
    setReviewText(detail?.review?.content ?? detail?.content ?? "");
  }, [detail?.digest_id, detail?.content, detail?.review?.review_id]);

  async function action(path: string, body?: object) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const record = await read(path, { method: "POST", ...(body ? {
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      } : {}) });
      if (!alive.current) return;
      await refresh(path === root ? record.digest_id : selected.current ?? undefined);
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : c.failure);
    } finally { if (alive.current) { setBusy(false); setConsent(false); } }
  }

  function generate() {
    try { void action(root, buildSummaryRequest(language, provider, model, consent, null, policy)); }
    catch { setError(c.failure); }
  }
  function save(approved: boolean) {
    if (!detail) return;
    void action(`${root}/${encodeURIComponent(detail.digest_id)}/reviews`, {
      content: reviewText, approved, expected_review_id: detail.review?.review_id ?? null,
    });
  }
  function exportText() {
    if (!detail?.evidence_available) return;
    const url = URL.createObjectURL(new Blob([detail.review?.content ?? detail.content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url;
    link.download = `project-digest-${detail.digest_id}.md`; link.click(); URL.revokeObjectURL(url);
  }

  const canEdit = detail?.status === "completed" && detail.evidence_available && !detail.stale && !detail.review?.approved;
  return <section className="history-post-event" data-testid="project-digest" aria-label={t.title}>
    <h3>{t.title}</h3><p>{t.scope}</p><p>{t.limit}</p>
    <button type="button" className="secondary-button" disabled={busy} onClick={() => { setError(""); void refresh(); }}>{c.refresh}</button>
    {loading && <p role="status">{c.generating}</p>}
    {error && <p role="alert" className="error-box">{error}</p>}
    {listing?.input ? <>
      <p>{t.included}: {listing.input.session_count} · {t.excluded}: {listing.input.excluded_running_count}</p>
      {listing.input.interrupted_count > 0 && <p role="status">{t.interrupted}</p>}
    </> : <p role="status">{t.prerequisite} {listing?.input_error}</p>}
    <div className="history-post-actions">
      <label>{c.summaryLanguage}<select value={language} disabled={busy || running}
        onChange={event => { setLanguage(event.target.value as SummaryLanguage); setConsent(false); }}>
        <option value="ko">{c.korean}</option><option value="en">{c.english}</option><option value="ja">{c.japanese}</option>
      </select></label>
      <label>{c.summaryProvider}<select value={provider} disabled={busy || running}
        onChange={event => { setProvider(event.target.value as SummaryProvider); setConsent(false); }}>
        <option value="ollama">{c.localProvider}</option><option value="openai-compatible">{c.externalProvider}</option>
      </select></label>
      <label>{c.modelId}<input value={model} maxLength={128} placeholder={c.defaultModel} disabled={busy || running}
        onChange={event => { setModel(event.target.value); setConsent(false); }} /></label>
    </div>
    {!policy && <p role="status">{c.providerPolicyUnknown}</p>}
    {needsConsent && <label><input type="checkbox" checked={consent} disabled={busy || running}
      onChange={event => setConsent(event.target.checked)} />{c.externalConsent}</label>}
    <button type="button" className="secondary-button" disabled={busy || loading || running || !listing?.input || !policy || (needsConsent && !consent)}
      onClick={generate}>{t.generate}</button>
    {listing?.versions.length ? <label>{t.versions}<select value={selected.current ?? ""} disabled={busy || loading}
      onChange={event => { setDetail(null); void refresh(event.target.value); }}>
      {listing.versions.map(version => <option key={version.digest_id} value={version.digest_id}>
        {version.created_at} · {version.language.toUpperCase()} · {version.status}
      </option>)}
    </select></label> : <p>{t.none}</p>}
    {detail && <>
      {detail.stale && <p role="alert">{t.stale}</p>}
      {detail.status === "running" && <><p role="status">{c.generating}</p>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => void action(`${root}/${encodeURIComponent(detail.digest_id)}/cancel`)}>{c.cancelSummary}</button></>}
      {detail.status === "error" && <p role="alert">{detail.error || c.failure}</p>}
      {!detail.evidence_available && <p role="status">{t.unavailable}</p>}
      {detail.status === "completed" && detail.evidence_available && <div className="history-post-summary">
        <small>{detail.provider} · {detail.model} · {detail.language.toUpperCase()} · {detail.prompt_version}</small>
        <p>{c.endpointUnrecorded}</p><p role="status">{detail.review?.approved ? c.approved : c.unreviewed}</p>
        <label>{c.reviewText}<textarea value={reviewText} rows={10} maxLength={15000} readOnly={!canEdit} disabled={busy}
          onChange={event => setReviewText(event.target.value)} /></label>
        <div className="history-post-actions">
          <button type="button" className="secondary-button" disabled={!canEdit || busy || !reviewText.trim()} onClick={() => save(false)}>{c.saveReview}</button>
          <button type="button" className="secondary-button" disabled={!canEdit || busy || !reviewText.trim()} onClick={() => save(true)}>{c.approveReview}</button>
          <button type="button" className="secondary-button" onClick={exportText}>{c.exportMarkdown}</button>
          <a className="secondary-button" href={`${root}/${encodeURIComponent(detail.digest_id)}/export`} download>{t.export}</a>
        </div>
        <details><summary>{c.originalDraft}</summary><pre>{detail.content}</pre></details>
        <details><summary>{t.evidence}</summary><pre>{detail.snapshot?.source}</pre></details>
        <ul>{detail.snapshot?.citations.filter(item => (detail.review?.content ?? detail.content).includes(item.marker)).map(item => <li key={item.alias}>
          <button type="button" className="secondary-button" onClick={() => onOpenSession(item.session_id)}>
            {t.source}: {detail.snapshot?.sessions.find(session => session.session_id === item.session_id)?.title} · {item.segment_id} · {item.start_ms / 1000}s
          </button>
          {item.transcript_id && <small>{item.transcript_id}</small>}
        </li>)}</ul>
      </div>}
    </>}
  </section>;
}
