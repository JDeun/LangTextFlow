import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_PACKAGE_COPY } from "./projectPackageCopy";

interface Props {
  apiUrl: string; projectId: string; locale: Locale;
  onRestored: (projectId: string) => Promise<void>;
}
interface Prepared { export_id: string; project_id: string; size_bytes: number; sha256: string }
interface Receipt { import_id: string; project_id: string; created_at: string }

function localEndpoint(value: string) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      && (url.hostname === "localhost" || url.hostname === "[::1]" || /^127\.\d+\.\d+\.\d+$/.test(url.hostname));
  } catch { return false; }
}

export function ProjectPackageControls(props: Props) {
  return <ScopedPackageControls key={`${props.apiUrl}\n${props.projectId}`} {...props} />;
}

function ScopedPackageControls({ apiUrl, projectId, locale, onRestored }: Props) {
  const t = PROJECT_PACKAGE_COPY[locale];
  const root = `${apiUrl}/api/v1/project-packages`;
  const local = localEndpoint(apiUrl);
  const selected = !["", "all", "unassigned"].includes(projectId);
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [repairs, setRepairs] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const alive = useRef(true);
  const epoch = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const preparedRef = useRef<Prepared | null>(null);

  async function read(path: string, init?: RequestInit) {
    const response = await fetch(path, init);
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : t.failure);
    return data;
  }
  function valid(value: unknown, expected: string | null = projectId): value is Prepared {
    if (!value || typeof value !== "object") return false;
    const p = value as Prepared;
    return /^[a-f0-9]{32}$/.test(p.export_id) && typeof p.project_id === "string"
      && (expected === null || p.project_id === expected)
      && Number.isSafeInteger(p.size_bytes) && p.size_bytes > 0 && p.size_bytes <= 1073741824
      && /^[a-f0-9]{64}$/.test(p.sha256);
  }
  function forget(value: Prepared | null) {
    if (value && local) void fetch(`${root}/${value.export_id}`, { method: "DELETE" }).catch(() => {});
  }

  async function refresh() {
    if (!local || busy) return;
    const request = ++epoch.current;
    setError("");
    try {
      const data = await read(root);
      if (!alive.current || request !== epoch.current) return;
      if (!data || !Array.isArray(data.imports) || data.imports.length > 20
        || data.imports.some((r: Receipt) => !r || typeof r.import_id !== "string"
          || typeof r.project_id !== "string" || typeof r.created_at !== "string")) throw new Error(t.failure);
      if (data.repairs !== undefined && (!Array.isArray(data.repairs) || data.repairs.length > 20)) throw new Error(t.failure);
      setRepairs(data.repairs?.length ?? 0);
      const next = valid(data.prepared, null) ? data.prepared : null;
      preparedRef.current = next; setPrepared(next); setReceipts(data.imports);
    } catch (reason) {
      if (alive.current && request === epoch.current) setError(reason instanceof Error ? reason.message : t.failure);
    }
  }

  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false; epoch.current++; controller.current?.abort(); forget(preparedRef.current);
    };
    // The keyed scope remounts for endpoint/project changes. Locale keeps ownership.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function action(kind: "export" | "restore") {
    if (!local || busy || (kind === "export" ? !selected : !file)) return;
    const request = ++epoch.current;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setError(""); setMessage("");
    try {
      if (kind === "restore" && (!file || file.size > 1073741824 || file.size < 22)) throw new Error(t.failure);
      const data = await read(kind === "export" ? `${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/package` : `${root}/restore`, {
        method: "POST", signal: abort.signal,
        ...(kind === "restore" ? { headers: { "Content-Type": "application/zip" }, body: file } : {}),
      });
      if (!alive.current || request !== epoch.current) {
        if (kind === "export" && valid(data)) forget(data);
        return;
      }
      if (kind === "export") {
        if (!valid(data)) throw new Error(t.failure);
        preparedRef.current = data; setPrepared(data);
      } else {
        if (!data || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(data.project_id)
          || !/^[a-f0-9]{32}$/.test(data.import_id)) throw new Error(t.failure);
        setFile(null); setMessage(t.restored);
        await onRestored(data.project_id);
      }
    } catch (reason) {
      if (alive.current && request === epoch.current) setError(reason instanceof Error ? reason.message : t.failure);
    } finally {
      if (alive.current && request === epoch.current) { setBusy(false); controller.current = null; }
    }
  }

  return <section className="history-post-event" data-testid="project-package" aria-label={t.title}>
    <h3>{t.title}</h3><p>{t.privacy}</p><p>{t.scope}</p><p>{t.conflict}</p>
    {!local && <p role="alert">{t.local}</p>}
    {!selected && <p>{t.select}</p>}
    <div className="history-post-actions">
      <button type="button" className="secondary-button" disabled={!local || busy || !selected || !!prepared}
        onClick={() => void action("export")}>{t.prepare}</button>
      <button type="button" className="secondary-button" disabled={!local || busy} onClick={() => void refresh()}>{t.refresh}</button>
    </div>
    {prepared && <>
      <p>{prepared.size_bytes} {t.size} · SHA-256 <code>{prepared.sha256}</code></p>
      <div className="history-post-actions">
        {prepared.project_id === projectId && <a className="secondary-button" href={`${root}/${prepared.export_id}/download`} download
          onClick={() => { preparedRef.current = null; setPrepared(null); setMessage(t.started); }}>{t.download}</a>}
        <button type="button" className="secondary-button" disabled={busy} onClick={() => {
          forget(preparedRef.current); preparedRef.current = null; setPrepared(null);
        }}>{t.discard}</button>
      </div><small>{t.expire}</small>
    </>}
    <label>{t.file}<input type="file" accept=".zip,application/zip" disabled={!local || busy || !!prepared}
      onChange={event => { setFile(event.target.files?.[0] ?? null); setError(""); setMessage(""); }} /></label>
    <button type="button" className="secondary-button" disabled={!local || busy || !file || !!prepared}
      onClick={() => void action("restore")}>{t.restore}</button>
    {busy && <p role="status">{t.busy}</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert" className="error-box">{error}</p>}
    {repairs > 0 && <p role="alert">{t.repair} ({repairs})</p>}
    {receipts.length > 0 && <details><summary>{t.receipts}</summary><ul>{receipts.map(receipt =>
      <li key={receipt.import_id}>{receipt.created_at} · {receipt.project_id}</li>)}</ul></details>}
  </section>;
}
