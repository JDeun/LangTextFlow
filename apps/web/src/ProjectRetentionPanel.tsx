import { useEffect, useRef, useState } from "react";
import type { Locale } from "./locales";
import { PROJECT_RETENTION_COPY } from "./projectRetentionCopy";

interface Policy { project_id: string; days: number | null; revision: number }
interface Receipt { operation_id: string; project_id: string; policy_revision: number; targets_sha256: string; recording_count: number; completed_at: string; deleted_session_count: number; media_cleanup_pending_count: number; media_cleanup_blocked_count: number }
interface Status { policy: Policy; receipts: Receipt[]; operation: { operation_id: string; phase: string; error: string | null } | null }
interface Preview { policy: Policy; targets_sha256: string; evaluated_at: string; cutoff: string | null; candidates: {session_id:string;title:string;ended_at:string;recording_count:number}[]; excluded_running_count: number; excluded_unverifiable_count: number;expired_session_count:number;remaining_session_count:number;remaining_recording_count:number }
interface Props { apiUrl: string; projectId: string; locale: Locale; onApplied: (ids: string[]) => void }

export function ProjectRetentionPanel(props: Props) {
  return <ScopedRetention key={`${props.apiUrl}\n${props.projectId}`} {...props} />;
}

function ScopedRetention({ apiUrl, projectId, locale, onApplied }: Props) {
  const t=PROJECT_RETENTION_COPY[locale], root=`${apiUrl}/api/v1/projects/${encodeURIComponent(projectId)}/retention`;
  const [status,setStatus]=useState<Status|null>(null),[preview,setPreview]=useState<Preview|null>(null);
  const [days,setDays]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState("");
  const [mode,setMode]=useState<"none"|"days">("none");
  const [pendingApply,setPendingApply]=useState(false);
  const alive=useRef(false), epoch=useRef(0), operation=useRef<{id:string;ids:string[];revision:number;sha:string}|null>(null), acknowledged=useRef(new Set<string>());
  const currentCopy=useRef(t); currentCopy.current=t;

  async function read(path:string,init?:RequestInit) {
    const response=await fetch(path,init);
    if (!response.ok) { const body=await response.json().catch(()=>null); throw new Error(typeof body?.detail === "string"?body.detail:currentCopy.current.failure); }
    return await response.json();
  }
  function acknowledge(receipts:Receipt[]) {
    const own=operation.current;
    if (own && receipts.some(r=>r.operation_id===own.id&&r.policy_revision===own.revision&&r.targets_sha256===own.sha&&r.deleted_session_count===own.ids.length) && !acknowledged.current.has(own.id)) {
      acknowledged.current.add(own.id); onApplied(own.ids);
      setPreview(null);
    }
  }
  function validReceipt(r:Receipt) {
    return !!r&&r.project_id===projectId&&typeof r.operation_id==="string"&&Number.isInteger(r.policy_revision)&&r.policy_revision>0
      && /^[a-f0-9]{64}$/.test(r.targets_sha256)&&typeof r.completed_at==="string"&&Number.isFinite(Date.parse(r.completed_at))
      && [r.deleted_session_count,r.recording_count,r.media_cleanup_pending_count,r.media_cleanup_blocked_count].every(n=>Number.isInteger(n)&&n>=0)
      && r.deleted_session_count<=100&&r.recording_count<=64&&r.media_cleanup_pending_count<=r.recording_count&&r.media_cleanup_blocked_count<=r.media_cleanup_pending_count;
  }
  async function refresh(resetForm=false) {
    const request=++epoch.current;
    try {
      const data=await read(root) as Status;
      if (!alive.current || request!==epoch.current) return;
      if (!data?.policy || data.policy.project_id!==projectId || !Number.isInteger(data.policy.revision)
        || data.policy.revision<0 || (data.policy.days!==null && (!Number.isInteger(data.policy.days)||data.policy.days<1||data.policy.days>36500))
        || !Array.isArray(data.receipts) || data.receipts.length>20 || data.receipts.some(r=>!validReceipt(r))) throw new Error(t.failure);
      setStatus(data);setPreview(old=>old&&old.policy.revision!==data.policy.revision?null:old);
      if(resetForm){setMode(data.policy.days===null?"none":"days");setDays(String(data.policy.days??30));} acknowledge(data.receipts);
    } catch(reason) { if(alive.current&&request===epoch.current) {setStatus(null);setPreview(null);setError(reason instanceof Error?reason.message:t.failure);} }
  }
  useEffect(()=>{alive.current=true;void refresh(true);return()=>{alive.current=false;epoch.current++;};},[root]);
  const running=status?.operation?.phase==="running";
  const cleanup=status?.receipts.some(r=>r.media_cleanup_pending_count>r.media_cleanup_blocked_count);
  useEffect(()=>{if(!running&&!cleanup&&!pendingApply)return;const timer=setInterval(()=>{void refresh();},2000);return()=>clearInterval(timer);},[running,cleanup,pendingApply]);

  async function action(kind:"save"|"preview"|"apply") {
    if(busy||!status)return;
    if(kind==="apply" && (!preview?.candidates.length || !window.confirm(t.confirm)))return;
    const request=++epoch.current, current=()=>alive.current&&request===epoch.current;
    setBusy(true);setError("");setNotice("");
    try {
      if(kind==="save") {
        await read(root,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({days:mode==="none"?null:Number(days),expected_revision:status.policy.revision})});
        if(current()){setPreview(null);await refresh(true);}
      } else if(kind==="preview") {
        const data=await read(`${root}/preview`,{method:"POST"}) as Preview;
        if(current()) {
          if(data?.policy?.project_id!==projectId || data.policy.revision!==status.policy.revision || !/^[a-f0-9]{64}$/.test(data.targets_sha256)
            || data.policy.days!==status.policy.days
            || ![data.excluded_running_count,data.excluded_unverifiable_count].every(n=>Number.isInteger(n)&&n>=0&&n<=1000)
            || !Number.isInteger(data.remaining_session_count)||data.remaining_session_count<0||data.remaining_session_count>1000
            || !Number.isSafeInteger(data.remaining_recording_count)||data.remaining_recording_count<0
            || !Array.isArray(data.candidates) || data.candidates.length>100 || data.candidates.some(r=>!r||typeof r.session_id!=="string"||typeof r.title!=="string"||typeof r.ended_at!=="string"||!Number.isFinite(Date.parse(r.ended_at))||!Number.isInteger(r.recording_count)||r.recording_count<0||r.recording_count>64)
            || new Set(data.candidates.map(r=>r.session_id)).size!==data.candidates.length
            || data.expired_session_count!==data.candidates.length+data.remaining_session_count)throw new Error(t.failure);
          setPreview(data);
        }
      } else if(preview) {
        const own={id:crypto.randomUUID(),ids:preview.candidates.map(r=>r.session_id),revision:preview.policy.revision,sha:preview.targets_sha256}; operation.current=own;
        setPendingApply(true);
        const receipt=await read(`${root}/apply`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operation_id:own.id,expected_revision:preview.policy.revision,targets_sha256:preview.targets_sha256})}) as Receipt;
        if(current()){
          if(!validReceipt(receipt)||receipt.operation_id!==own.id||receipt.targets_sha256!==own.sha||receipt.policy_revision!==own.revision||receipt.deleted_session_count!==own.ids.length)throw new Error(t.failure);
          acknowledge([receipt]);setPreview(null);await refresh();
        }
      }
    } catch(reason) { if(current()){setPreview(null);setError(reason instanceof Error?reason.message:t.failure);await refresh();} }
    finally {if(alive.current){setBusy(false);setPendingApply(false);}}
  }
  async function cancel() {
    const own=operation.current;if(!own)return;
    try {await read(`${root}/cancel`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({operation_id:own.id})});if(alive.current)setNotice(t.cancelling);}
    catch(reason){if(alive.current)setError(reason instanceof Error?reason.message:t.failure);}
  }
  const dirty=!!status&&(mode==="none"?status.policy.days!==null:days!==String(status.policy.days??""));
  const valid=mode==="none"||(!!days&&Number.isInteger(Number(days))&&Number(days)>=1&&Number(days)<=36500);
  return <section className="history-post-event" data-testid="project-retention" aria-label={t.title}>
    <h3>{t.title}</h3><p>{t.help}</p><small>{t.local}</small>
    <details><summary>{t.details}</summary><p>{t.scope}</p><p>{t.safety}</p><small>{t.limit}</small></details>
    {(error||status?.operation?.error)&&<p role="alert" className="error-box">{error||status?.operation?.error}</p>}{notice&&<p role="status">{notice}</p>}
    {!status&&!error&&<p role="status">{t.loading}</p>}
    <div className="history-post-actions">
      <label>{t.mode}<select value={mode} disabled={!status||busy||running} onChange={event=>{setMode(event.target.value as "none"|"days");setPreview(null);}}>
        <option value="none">{t.none}</option><option value="days">{t.reviewAge}</option>
      </select></label>
      {mode==="days"&&<label>{t.days}<input type="number" min={1} max={36500} step={1} value={days} disabled={busy||running} onChange={event=>{setDays(event.target.value);setPreview(null);}} /></label>}
    </div>
    <div className="history-post-actions">
      <button type="button" className="secondary-button" disabled={!status||busy||running||!valid||!dirty} onClick={()=>{void action("save");}}>{t.save}</button>
      <button type="button" className="secondary-button" disabled={!status||busy||running||dirty||status.policy.days===null} onClick={()=>{void action("preview");}}>{t.preview}</button>
      <button type="button" className="secondary-button" disabled={busy} onClick={()=>{setError("");setPreview(null);void refresh(true);}}>{t.refresh}</button>
      {pendingApply&&<button type="button" className="secondary-button" onClick={()=>{void cancel();}}>{t.cancel}</button>}
    </div>
    {running&&<p role="status">{t.busy}</p>}
    {preview&&<div><h4>{t.candidates}: {preview.candidates.length}</h4>
      <p>{t.excluded}: {preview.excluded_running_count} · {t.invalid}: {preview.excluded_unverifiable_count}</p>
      {preview.remaining_session_count>0&&<p role="status">{t.remaining}: {preview.remaining_session_count} · {t.remainingMedia}: {preview.remaining_recording_count}. {t.next}</p>}
      {!preview.candidates.length&&<p>{t.empty}</p>}
      <ul className="project-archive-list">{preview.candidates.map(item=><li key={item.session_id}><strong>{item.title}</strong><span>{t.ended}: {new Date(item.ended_at).toLocaleString(locale)} · {t.recordings}: {item.recording_count}</span></li>)}</ul>
      {!!preview.candidates.length&&<><p>{t.scope}</p><p>{t.safety}</p></>}
      <button type="button" className="secondary-button" disabled={busy||running||dirty||!preview.candidates.length} onClick={()=>{void action("apply");}}>{t.apply}</button>
    </div>}
    {!!status?.receipts.length&&<div><h4>{t.receipts}</h4><ul className="project-archive-list">{status.receipts.map(receipt=><li key={receipt.operation_id}>
      <span>{new Date(receipt.completed_at).toLocaleString(locale)} · {t.deleted}: {receipt.deleted_session_count}</span>
      <small>{t.pending}: {receipt.media_cleanup_pending_count} · {t.blocked}: {receipt.media_cleanup_blocked_count}</small>
    </li>)}</ul></div>}
  </section>;
}
