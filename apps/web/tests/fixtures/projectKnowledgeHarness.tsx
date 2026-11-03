import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ProjectKnowledgePanel } from "../../src/ProjectKnowledgePanel";
import { PROJECT_KNOWLEDGE_COPY } from "../../src/projectKnowledgeCopy";
import { ProjectArchiveIndex } from "../../src/ProjectArchiveIndex";
import { POST_EVENT_COPY } from "../../src/postEventCopy";
import { PROJECT_DIGEST_COPY } from "../../src/projectDigestCopy";

export async function runProjectKnowledgeChecks() {
  const assert = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; i < 100 && !check(); i++) await tick();
    assert(check(), message);
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host), originalFetch = window.fetch, originalConfirm = window.confirm;
  const t = PROJECT_KNOWLEDGE_COPY.en;
  const item = (pid: string) => ({ project_id: pid, knowledge_id: `note-${pid}`, kind: "todo", title: `Owned ${pid}`,
    text: "Original 성령 / 原文 🙏", done: false, revision: 1, content_sha256: "a".repeat(64) });
  const calls: { path: string; init?: RequestInit }[] = [];
  let changed = 0, held: Response | null = null, release: (() => void) | null = null;
  let heldMutation: Promise<Response> | null = null;
  window.confirm = () => true;
  window.fetch = async (input, init) => {
    const path = String(input); calls.push({ path, init });
    if (init?.method && heldMutation) { const response = heldMutation; heldMutation = null; return response; }
    if (!init?.method && held) { const response = held; held = null; return response; }
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    if (init?.method) return Response.json(item("A"));
    return Response.json([item(path.includes("/A/") ? "A" : "B")]);
  };
  const render = (pid: string) => flushSync(() => root.render(<ProjectKnowledgePanel apiUrl="http://fixture" projectId={pid} locale="en" onChanged={() => changed++} />));
  const button = (label: string) => Array.from(host.querySelectorAll("button")).find(b => b.textContent === label)!;
  const input = (selector: string, value: string) => {
    const el = host.querySelector(selector)!;
    assert(el, `missing form control: ${selector}`);
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  };
  try {
    render("A"); await until(() => !!host.querySelector("h4"), "materials did not load");
    button(t.edit).click(); await tick();
    assert(host.querySelector("textarea")?.value === item("A").text, "original UTF-8 text lost");
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(); await tick();
    button(t.save).click(); await until(() => changed === 1, "TODO did not save");
    const saved = calls.find(c => c.init?.method === "PUT")!;
    assert(JSON.parse(String(saved.init!.body)).expected_revision === 1 && JSON.parse(String(saved.init!.body)).done === true, "revision/TODO payload missing");
    await until(() => !button(t.add).disabled, "saved list did not reload");
    button(t.add).click(); await until(() => !!host.querySelector("select"), "new material form absent");
    input("select", "handout"); await tick();
    const fileInput = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    const files = new DataTransfer(); files.items.add(new File(["Text 原文 🙏\r\n"], "owned.md", { type: "text/markdown" }));
    fileInput.files = files.files; fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    await until(() => host.querySelector("textarea")?.value === "Text 原文 🙏\r\n" || host.querySelector("textarea")?.value === "Text 原文 🙏\n", "UTF-8 handout reader did not populate reviewed editor");
    const bad = new DataTransfer(); bad.items.add(new File([new Uint8Array([255])], "invalid.txt"));
    fileInput.files = bad.files; fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    await until(() => host.querySelector('[role="alert"]')?.textContent === t.fileError, "non-UTF-8 input accepted");
    assert(host.querySelector("textarea")!.value.includes("原文"), "invalid import destroyed reviewed text");
    let releaseFile: (() => void) | null = null;
    const delayed = new File(["obsolete"], "deferred.txt");
    delayed.arrayBuffer = () => new Promise(resolve => { releaseFile = () => resolve(new TextEncoder().encode("obsolete").buffer); });
    const delayedFiles = new DataTransfer(); delayedFiles.items.add(delayed); fileInput.files = delayedFiles.files;
    fileInput.dispatchEvent(new Event("change", { bubbles: true })); await until(() => !!releaseFile, "file not deferred");
    input("textarea", "Current user text"); releaseFile!(); await tick(); await tick();
    assert(host.querySelector("textarea")!.value === "Current user text", "late file read overwrote user edits");
    button(t.cancel).click(); await tick();
    // A deferred JSON response from A cannot enter the replacement B scope.
    held = new Response(); held.json = () => new Promise(resolve => { release = () => resolve([item("A")]); });
    button(t.refresh).click(); await until(() => !!release, "JSON not deferred");
    render("B"); await until(() => host.textContent!.includes("Owned B"), "replacement project not loaded");
    release!(); await tick(); await tick();
    assert(!host.textContent!.includes("Owned A"), "late JSON crossed project scope");
    button(t.edit).click(); await tick();
    let releaseMutation: ((response: Response) => void) | null = null;
    heldMutation = new Promise(resolve => { releaseMutation = resolve; });
    const beforeLateSave = changed;
    button(t.save).click(); await until(() => heldMutation === null, "save not deferred");
    render("A"); await until(() => host.textContent!.includes("Owned A"), "new project after deferred save absent");
    releaseMutation!(Response.json(item("B"))); await tick(); await tick();
    assert(changed === beforeLateSave && host.textContent!.includes("Owned A"), "late mutation invalidated replacement scope");
    render("B"); await until(() => host.textContent!.includes("Owned B"), "delete project not restored");
    button(t.remove).click(); await until(() => calls.some(c => c.init?.method === "DELETE"), "delete absent");
    assert(calls.find(c => c.init?.method === "DELETE")!.path.endsWith("note-B?expected_revision=1"), "delete identity/revision missing");
    // Mutations completing after unmount cannot invalidate a different project digest.
    await tick(); held = new Response(); held.json = () => new Promise(resolve => { release = () => resolve([item("B")]); });
    render("A"); await until(() => held === null, "unmount fixture not fetched");
    flushSync(() => root.unmount()); release?.(); await tick();
    await compositeDraftPreservation();
    return "materials actual React: original Unicode, TODO/revision save, UTF-8/invalid file handling, late file/user-edit/save/JSON scope/unmount fences, deletion identity";
  } finally { try { flushSync(() => root.unmount()); } catch { /* already unmounted */ } host.remove(); window.fetch = originalFetch; window.confirm = originalConfirm; }
}

async function compositeDraftPreservation() {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host), originalFetch = window.fetch;
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; i < 100 && !check(); i++) await tick();
    if (!check()) throw new Error(message);
  };
  const t = PROJECT_KNOWLEDGE_COPY.en, c = POST_EVENT_COPY.en, d = PROJECT_DIGEST_COPY.en;
  let stale = false, removed = false, listingReads = 0;
  const record = (id: string) => ({ digest_id:id, status:"completed", language:"en", provider:"ollama", model:"fixture",
    prompt_version:"fixture", input_sha256:"a".repeat(64), created_at:"2026-11-03T12:00:00Z", stale, error:null, review:null,
    content:`Original ${id} [p0001.s000001 at 1s]`, evidence_available:true,
    snapshot:{ source:"[p0001.s000001 at 1s] {} Original speech", sessions:[{session_id:"s",title:"Source",started_at:"2026-11-03T12:00:00Z",transcript_id:null}],
      citations:[{alias:"p0001.s000001",marker:"[p0001.s000001 at 1s]",session_id:"s",segment_id:"original",start_ms:1000,transcript_id:null}] } });
  window.fetch = async (input, init) => {
    const path=String(input);
    if (path.endsWith("/knowledge") || path.includes("/knowledge/")) {
      if (init?.method === "DELETE") { removed=true; return new Response(null,{status:204}); }
      if (init?.method === "PUT") { stale=true; return Response.json({}); }
      return Response.json([{project_id:"A",knowledge_id:"owned",kind:"brief",title:"Owned context",text:"Operator text",done:false,revision:1,content_sha256:"a".repeat(64)}]);
    }
    if (path.endsWith("/digests")) { listingReads++; return Response.json({versions:removed?[]:[record("new"),record("selected-old")], input:{input_sha256:"b".repeat(64),session_count:1,excluded_running_count:0,interrupted_count:0},input_error:null,summary_external_providers:["openai-compatible"]}); }
    if (path.includes("/digests/")) return Response.json(record(path.split("/").at(-1)!));
    return Response.json({project:{project_id:"A",title:"Owned"},pagination:{limit:25,offset:0,total_sessions_at_query:0,has_more:false},sessions:[]});
  };
  const panel = () => host.querySelector('[data-testid="project-digest"]')!;
  const materialButton = (label:string) => Array.from(host.querySelectorAll('[data-testid="project-knowledge"] button')).find(b=>b.textContent===label) as HTMLButtonElement;
  const control = (label:string) => Array.from(panel().querySelectorAll("label")).find(el=>el.textContent?.startsWith(label))!;
  const change = (element:HTMLInputElement|HTMLTextAreaElement|HTMLSelectElement,value:string) => {
    const proto=element instanceof HTMLSelectElement?HTMLSelectElement.prototype:element instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,"value")!.set!.call(element,value);
    element.dispatchEvent(new Event(element instanceof HTMLSelectElement?"change":"input",{bubbles:true}));
  };
  try {
    flushSync(()=>root.render(<ProjectArchiveIndex apiUrl="http://fixture" projectId="A" locale="en" onOpenSession={()=>{}} />));
    await until(()=>!!panel().querySelector("textarea"),"composite digest did not load");
    change(control(d.versions).querySelector("select")!,"selected-old");
    await until(()=>panel().querySelector("textarea")?.value.startsWith("Original selected-old")??false,"old version not selected");
    change(panel().querySelector("textarea")!,"UNSAVED operator review [p0001.s000001 at 1s]");
    change(control(c.summaryProvider).querySelector("select")!,"openai-compatible");
    change(control(c.modelId).querySelector("input")!,"owned-custom-model"); await tick();
    const before=listingReads; materialButton(t.refresh).click();
    await until(()=>listingReads>before && !control(d.versions).querySelector("select")!.disabled,"composite materials refresh incomplete");
    if (panel().querySelector("textarea")?.value!=="UNSAVED operator review [p0001.s000001 at 1s]") throw new Error("material reload destroyed unsaved project review");
    if (control(d.versions).querySelector("select")!.value!=="selected-old" || control(c.summaryProvider).querySelector("select")!.value!=="openai-compatible" || control(c.modelId).querySelector("input")!.value!=="owned-custom-model") throw new Error("material reload reset version/provider/model selection");
    materialButton(t.edit).click(); await until(()=>!!host.querySelector('[data-testid="project-knowledge"] textarea'),"material editor absent");
    materialButton(t.save).click();
    await until(()=>panel().textContent?.includes(d.stale)??false,"material change did not refresh stale state");
    const save = Array.from(panel().querySelectorAll("button")).find(b=>b.textContent===c.saveReview) as HTMLButtonElement;
    if (!save.disabled || panel().querySelector("textarea")?.value!=="UNSAVED operator review [p0001.s000001 at 1s]") throw new Error("stale material edit lost draft or permitted review");
    await until(()=>!materialButton(t.remove).disabled,"material save still busy");
    materialButton(t.remove).click();
    await until(()=>!panel().querySelector("textarea"),"material deletion retained erased digest version");
  } finally { flushSync(()=>root.unmount()); host.remove(); window.fetch=originalFetch; }
}
