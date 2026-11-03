import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ProjectKnowledgePanel } from "../../src/ProjectKnowledgePanel";
import { PROJECT_KNOWLEDGE_COPY } from "../../src/projectKnowledgeCopy";

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
  window.confirm = () => true;
  window.fetch = async (input, init) => {
    const path = String(input); calls.push({ path, init });
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
    button(t.cancel).click(); await tick();
    // A deferred JSON response from A cannot enter the replacement B scope.
    held = new Response(); held.json = () => new Promise(resolve => { release = () => resolve([item("A")]); });
    button(t.refresh).click(); await until(() => !!release, "JSON not deferred");
    render("B"); await until(() => host.textContent!.includes("Owned B"), "replacement project not loaded");
    release!(); await tick(); await tick();
    assert(!host.textContent!.includes("Owned A"), "late JSON crossed project scope");
    button(t.remove).click(); await until(() => calls.some(c => c.init?.method === "DELETE"), "delete absent");
    assert(calls.find(c => c.init?.method === "DELETE")!.path.endsWith("note-B?expected_revision=1"), "delete identity/revision missing");
    // Mutations completing after unmount cannot invalidate a different project digest.
    await tick(); held = new Response(); held.json = () => new Promise(resolve => { release = () => resolve([item("B")]); });
    render("A"); await until(() => held === null, "unmount fixture not fetched");
    flushSync(() => root.unmount()); release?.(); await tick();
    return "materials actual React: original Unicode, TODO/revision save, reviewed UTF-8 handout, deletion identity, deferred JSON scope/unmount fences";
  } finally { try { flushSync(() => root.unmount()); } catch { /* already unmounted */ } host.remove(); window.fetch = originalFetch; window.confirm = originalConfirm; }
}
