import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ProjectRetentionPanel } from "../../src/ProjectRetentionPanel";
import { PROJECT_RETENTION_COPY } from "../../src/projectRetentionCopy";

export async function runProjectRetentionChecks() {
  const t=PROJECT_RETENTION_COPY.en;
  const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
  const originalFetch=window.fetch,originalConfirm=window.confirm;
  const calls:{path:string;method:string;body:Record<string,unknown>}[]=[], applied:string[][]=[];
  const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
  const until=async(check:()=>boolean,message:string)=>{for(let i=0;i<150&&!check();i++)await tick();if(!check())throw new Error(message);};
  let revision=0,days:number|null=null,receipts:unknown[]=[],held:Promise<Response>|null=null,release:((r:Response)=>void)|null=null,malformed=false,stale=false;
  const policy=(pid:string)=>({project_id:pid,days,revision});
  const receipt=(body:Record<string,unknown>,pid="A")=>({operation_id:body.operation_id,project_id:pid,policy_revision:body.expected_revision,targets_sha256:body.targets_sha256,deleted_session_count:1,recording_count:1,completed_at:"2026-11-04T00:00:00Z",media_cleanup_pending_count:0,media_cleanup_blocked_count:0});
  window.confirm=()=>true;
  window.fetch=async(input,init)=>{
    const path=String(input),method=init?.method||"GET",body=init?.body?JSON.parse(String(init.body)):{};
    calls.push({path,method,body});const pid=path.includes("/B/")?"B":"A";
    if(method==="GET")return Response.json(malformed?{policy:{revision:"bad"}}:{policy:policy(pid),receipts,operation:null});
    if(method==="PUT"){days=body.days;revision++;return Response.json(policy(pid));}
    if(path.endsWith("/preview"))return Response.json({policy:policy(pid),targets_sha256:"a".repeat(64),evaluated_at:"2026-11-04T00:00:00Z",cutoff:"2026-10-05T00:00:00Z",excluded_running_count:1,excluded_unverifiable_count:1,expired_session_count:2,remaining_session_count:1,remaining_recording_count:1,candidates:[{session_id:`old-${pid}`,title:`Owned expired ${pid}`,ended_at:"2026-08-01T00:00:00Z",recording_count:1}]});
    if(path.endsWith("/cancel"))return Response.json({cancel_requested:true},{status:202});
    if(held){const response=held;held=null;return response;}
    if(stale)return Response.json({detail:"Retention policy or candidates changed; preview again"},{status:409});
    const result=receipt(body,pid);receipts=[result];return Response.json(result);
  };
  const render=(pid="A")=>flushSync(()=>root.render(<ProjectRetentionPanel apiUrl="http://fixture" projectId={pid} locale="en" onApplied={ids=>applied.push(ids)} />));
  const button=(label:string)=>Array.from(host.querySelectorAll("button")).find(b=>b.textContent===label)!;
  const change=(el:HTMLSelectElement|HTMLInputElement,value:string)=>{Object.getOwnPropertyDescriptor(el instanceof HTMLSelectElement?HTMLSelectElement.prototype:HTMLInputElement.prototype,"value")!.set!.call(el,value);el.dispatchEvent(new Event(el instanceof HTMLSelectElement?"change":"input",{bubbles:true}));};
  const preview=async()=>{button(t.preview).click();await until(()=>!!button(t.apply)&&!button(t.apply).disabled,"expired preview absent");};
  try {
    render();await until(()=>!!host.querySelector("select:not(:disabled)"),"retention settings unavailable");
    if(calls.some(c=>c.method!=="GET")||!button(t.preview).disabled||host.querySelectorAll("li").length)throw new Error("default retention started a mutation or preview");
    change(host.querySelector("select")!,"days");await tick();
    change(host.querySelector('input[type="number"]')!,"");await tick();
    if(!host.querySelector('input[type="number"]')||!button(t.save).disabled)throw new Error("clearing the day input changed mode or allowed invalid policy");
    change(host.querySelector('input[type="number"]')!,"30");await tick();button(t.save).click();
    await until(()=>!button(t.preview).disabled,"rule did not save");
    const saved=calls.find(c=>c.method==="PUT")!;
    if(saved.body.expected_revision!==0||saved.body.days!==30)throw new Error("retention save omitted revision/explicit days");
    await preview();if(!host.textContent?.includes("Owned expired A")||!host.textContent.includes(t.next))throw new Error("reviewed identity/remaining-batch guidance not shown");
    change(host.querySelector('input[type="number"]')!,"31");await tick();
    if(host.textContent?.includes("Owned expired A"))throw new Error("edited policy retained executable preview");
    button(t.refresh).click();await until(()=>!button(t.preview).disabled,"saved rule reload failed");await preview();
    stale=true;button(t.apply).click();await until(()=>host.querySelector('[role="alert"]')?.textContent?.includes("changed")??false,"stale execution not surfaced");
    if(applied.length||host.textContent?.includes("Owned expired A"))throw new Error("failed execution acknowledged or retained stale preview");
    stale=false;await until(()=>!button(t.preview).disabled,"stale attempt still busy");await preview();
    button(t.apply).click();await until(()=>applied.length===1,"committed receipt not acknowledged");
    if(applied[0][0]!=="old-A"||host.textContent?.includes("Owned expired A"))throw new Error("committed deletion did not clear exact reviewed scope");
    const firstApply=calls.find(c=>c.path.endsWith("/apply"))!;
    if(firstApply.body.expected_revision!==1||firstApply.body.targets_sha256!=="a".repeat(64))throw new Error("apply omitted reviewed revision/hash");
    receipts=[];await until(()=>!button(t.preview).disabled,"successful deletion still busy");await preview();
    held=new Promise(resolve=>{release=resolve;});button(t.apply).click();
    await until(()=>!!button(t.cancel),"pending deletion has no cancel control");button(t.cancel).click();
    await until(()=>calls.some(c=>c.path.endsWith("/cancel")),"cancel request missing");
    const lastApply=calls.filter(c=>c.path.endsWith("/apply")).at(-1)!;
    if(calls.find(c=>c.path.endsWith("/cancel"))!.body.operation_id!==lastApply.body.operation_id)throw new Error("cancel guessed operation ownership");
    release!(Response.json({detail:"Retention cancelled"},{status:409}));await until(()=>!button(t.preview).disabled,"cancelled request still busy");
    await preview();held=new Promise(resolve=>{release=resolve;});button(t.apply).click();await until(()=>held===null,"late apply not held");
    const delayed=calls.filter(c=>c.path.endsWith("/apply")).at(-1)!.body;
    render("B");await until(()=>!!host.querySelector("select:not(:disabled)"),"replacement project not loaded");
    release!(Response.json(receipt(delayed)));await tick();await tick();
    if(applied.length!==1)throw new Error("late receipt invalidated replacement project");
    malformed=true;button(t.refresh).click();await until(()=>!!host.querySelector('[role="alert"]'),"malformed status not shown");
    if(!button(t.preview).disabled)throw new Error("malformed metadata allowed destructive preview");
    return "retention actual React: default no mutation, policy CAS, reviewed revision/hash, stale/cancel failures, committed identity, scope/late-receipt fences, fail-closed metadata";
  } finally {flushSync(()=>root.unmount());host.remove();window.fetch=originalFetch;window.confirm=originalConfirm;}
}
