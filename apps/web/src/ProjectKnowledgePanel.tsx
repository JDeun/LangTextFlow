import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_KNOWLEDGE_COPY } from "./projectKnowledgeCopy";

type Kind = keyof typeof PROJECT_KNOWLEDGE_COPY.en.kinds;
interface Material {
  knowledge_id: string; project_id: string; kind: Kind; title: string; text: string;
  done: boolean; revision: number; content_sha256: string;
}
interface Props { apiUrl: string; projectId: string; locale: Locale; onChanged: () => void }

export function ProjectKnowledgePanel(props: Props) {
  return <ScopedKnowledgePanel key={`${props.apiUrl}\n${props.projectId}`} {...props} />;
}

function ScopedKnowledgePanel({ apiUrl, projectId, locale, onChanged }: Props) {
  const t = PROJECT_KNOWLEDGE_COPY[locale];
  const root = `${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/knowledge`;
  const [items, setItems] = useState<Material[] | null>(null);
  const [editing, setEditing] = useState<Material | "new" | null>(null);
  const [kind, setKind] = useState<Kind>("brief");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const alive = useRef(false);
  const epoch = useRef(0);
  const fileEpoch = useRef(0);
  const failure = useRef(t.failure);
  failure.current = t.failure;

  useEffect(() => {
    alive.current = true;
    const request = ++epoch.current;
    const controller = new AbortController();
    setItems(null);
    fetch(root, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(failure.current);
      const rows = await response.json() as Material[];
      if (!Array.isArray(rows) || rows.length > 100 || rows.some(row => !row
        || row.project_id !== projectId || typeof row.knowledge_id !== "string"
        || !Object.hasOwn(PROJECT_KNOWLEDGE_COPY.en.kinds, row.kind) || typeof row.title !== "string"
        || typeof row.text !== "string" || row.text.length > 40000
        || typeof row.done !== "boolean" || !Number.isInteger(row.revision)
        || row.revision < 1 || !/^[a-f0-9]{64}$/.test(row.content_sha256))) throw new Error(failure.current);
      if (alive.current && request === epoch.current) setItems(rows);
    }).catch(() => { if (alive.current && request === epoch.current) setError(failure.current); });
    return () => { alive.current = false; epoch.current++; fileEpoch.current++; controller.abort(); };
  }, [root, refresh, projectId]);

  function edit(item: Material | "new") {
    fileEpoch.current++; setEditing(item); setError(""); setNotice("");
    setKind(item === "new" ? "brief" : item.kind);
    setTitle(item === "new" ? "" : item.title);
    setText(item === "new" ? "" : item.text);
    setDone(item === "new" ? false : item.done);
  }

  async function readFile(file?: File) {
    if (!file || busy) return;
    const request = ++fileEpoch.current;
    try {
      if (file.size > 65536 || !/\.(txt|md|markdown)$/i.test(file.name)) throw new Error();
      const value = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer());
      if (!value.trim() || value.includes("\0") || [...value].length > 20000) throw new Error();
      if (!alive.current || request !== fileEpoch.current) return;
      setText(value); setError("");
    } catch { if (alive.current && request === fileEpoch.current) setError(t.fileError); }
  }

  async function mutate(item?: Material) {
    if (busy || (!item && !editing)) return;
    if (item && !window.confirm(t.deletion)) return;
    const request = ++epoch.current;
    const current = () => alive.current && epoch.current === request;
    const original = item ?? (editing !== "new" ? editing : null);
    setBusy(true); setError(""); setNotice(""); fileEpoch.current++;
    try {
      const url = original ? `${root}/${encodeURIComponent(original.knowledge_id)}` : root;
      const response = await fetch(item ? `${url}?expected_revision=${item.revision}` : url, {
        method: item ? "DELETE" : original ? "PUT" : "POST",
        ...(!item ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          kind, title, text, done: kind === "todo" && done, expected_revision: original?.revision ?? null,
        }) } : {}),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : t.failure);
      }
      if (!current()) return;
      setEditing(null); setRefresh(value => value + 1); setNotice(item ? "" : t.changed); onChanged();
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : t.failure); }
    finally { if (current()) setBusy(false); }
  }

  return <section className="history-post-event project-knowledge" data-testid="project-knowledge" aria-label={t.title}>
    <h3>{t.title}</h3><p>{t.help}</p><small>{t.limit}</small>
    <div className="history-post-actions">
      <button type="button" className="secondary-button" disabled={busy || !items} onClick={() => edit("new")}>{t.add}</button>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => { setError(""); setRefresh(v => v + 1); onChanged(); }}>{t.refresh}</button>
    </div>
    {!items && !error && <p role="status">{t.loading}</p>}
    {error && <p role="alert" className="error-box">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {editing && <form className="project-knowledge-editor" onSubmit={event => { event.preventDefault(); void mutate(); }}>
      <label>{t.kind}<select value={kind} disabled={busy} onChange={event => { fileEpoch.current++; setKind(event.target.value as Kind); }}>
        {Object.entries(t.kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>{t.name}<input required maxLength={160} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
      {kind === "handout" && <label>{t.file}<input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" disabled={busy}
        onChange={event => { void readFile(event.target.files?.[0]); event.target.value = ""; }} /><small>{t.fileHelp}</small></label>}
      <label>{t.text}<textarea required value={text} maxLength={40000} disabled={busy} rows={8} onChange={event => { fileEpoch.current++; setText(event.target.value); }} /></label>
      {kind === "todo" && <label><input type="checkbox" checked={done} disabled={busy} onChange={event => setDone(event.target.checked)} />{t.done}</label>}
      <div className="history-post-actions">
        <button type="submit" className="secondary-button" disabled={busy || !title.trim() || !text.trim() || [...text].length > 20000}>{t.save}</button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => { fileEpoch.current++; setEditing(null); }}>{t.cancel}</button>
      </div>
    </form>}
    {items?.length === 0 && <p>{t.empty}</p>}
    <ul className="project-knowledge-list">{items?.map(item => <li key={item.knowledge_id}>
      <h4>{item.title}</h4><small>{t.kinds[item.kind]} · {t.author} · {t.revision} {item.revision}{item.done ? ` · ${t.done}` : ""}</small>
      <details><summary>{t.text}</summary><p className="project-knowledge-text">{item.text}</p><small className="project-knowledge-hash">{t.hash}: {item.content_sha256}</small></details>
      <div className="history-post-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => edit(item)}>{t.edit}</button>
        <button type="button" className="secondary-button" disabled={busy} onClick={() => { void mutate(item); }}>{t.remove}</button></div>
    </li>)}</ul>
  </section>;
}
