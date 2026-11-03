import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ProjectPackageControls } from "../../src/ProjectPackageControls";
import { PROJECT_PACKAGE_COPY } from "../../src/projectPackageCopy";

export async function runProjectPackageChecks() {
  const t = PROJECT_PACKAGE_COPY.en;
  const assert = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 100; i++) await tick();
    assert(check(), message);
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const originalFetch = window.fetch;
  const calls: { path: string; method: string; body: BodyInit | null | undefined }[] = [];
  const restored: string[] = [];
  let held: Promise<Response> | null = null;
  let fail = false;
  let prepared: Record<string, unknown> | null = null;
  const data = (pid: string) => ({ export_id: "a".repeat(32), project_id: pid, size_bytes: 22000, sha256: "b".repeat(64) });
  window.fetch = async (input, init) => {
    const path = new URL(String(input)).pathname, method = init?.method || "GET";
    calls.push({ path, method, body: init?.body });
    if (method === "DELETE") { prepared = null; return new Response(null, { status: 204 }); }
    if (method === "POST") {
      if (held) { const response = held; held = null; return response; }
      if (fail) return Response.json({ detail: "ID collision fixture" }, { status: 409 });
      if (path.endsWith("/restore")) return Response.json({ import_id: "c".repeat(32), project_id: "restored-project" }, { status: 201 });
      prepared = data(path.split("/").at(-2)!);
      return Response.json(prepared, { status: 201 });
    }
    return Response.json({ prepared, imports: [] });
  };
  const render = (pid = "project-A", apiUrl = "http://127.0.0.1:8000") => flushSync(() => root.render(
    <ProjectPackageControls apiUrl={apiUrl} projectId={pid} locale="en" onRestored={async id => { restored.push(id); }} />,
  ));
  const button = (label: string) => Array.from(host.querySelectorAll("button")).find(el => el.textContent === label)!;
  const fileInput = () => host.querySelector<HTMLInputElement>('input[type="file"]')!;
  const choose = (file: File) => {
    const transfer = new DataTransfer(); transfer.items.add(file);
    fileInput().files = transfer.files;
    fileInput().dispatchEvent(new Event("change", { bubbles: true }));
  };
  try {
    render("all"); await tick(); await tick();
    assert(button(t.prepare).disabled && !fileInput().disabled, "fresh empty history cannot restore");
    const zip = new File([new Uint8Array(22)], "owned.zip", { type: "application/zip" });
    choose(zip); await tick(); button(t.restore).click();
    await until(() => restored.length === 1, "explicit selected ZIP did not call restore");
    assert(calls.at(-1)!.body === zip, "restore buffered or transformed selected File");
    assert(restored[0] === "restored-project", "restore selected wrong project");

    render("project-A"); await tick(); await tick();
    button(t.prepare).click();
    await until(() => !!host.querySelector("a[download]"), "prepared native attachment missing");
    assert(host.querySelector<HTMLAnchorElement>("a[download]")!.href.endsWith(`/project-packages/${"a".repeat(32)}/download`), "download used wrong identity");
    assert(button(t.prepare).disabled && fileInput().disabled, "prepared lease allowed overlapping import");
    button(t.discard).click(); await tick();
    assert(calls.at(-1)!.method === "DELETE" && !host.querySelector("a[download]"), "explicit discard not wired");

    // A prepare response whose JSON resolves after scope change must be discarded.
    let releaseJson!: (value: unknown) => void;
    const json = new Promise(resolve => { releaseJson = resolve; });
    const delayed = new Response(); Object.defineProperty(delayed, "json", { value: () => json });
    held = Promise.resolve(delayed);
    button(t.prepare).click(); await tick();
    render("project-B"); await tick(); await tick();
    releaseJson(data("project-A")); await tick(); await tick();
    assert(!host.querySelector("a[download]") && !button(t.prepare).disabled, "old project JSON installed a backup");
    assert(calls.at(-1)!.method === "DELETE", "late prepared ZIP leaked its lease");

    // A prior tab's prepared package can be explicitly discarded after reload,
    // but is never offered as the newly selected project's download.
    prepared = data("project-A");
    button(t.refresh).click(); await tick(); await tick();
    assert(!host.querySelector("a[download]") && !!button(t.discard), "foreign-scope prepared lease trapped the operator");
    button(t.discard).click(); await tick();

    fail = true;
    choose(zip); await tick(); button(t.restore).click();
    await until(() => host.textContent?.includes("ID collision fixture") ?? false, "restore collision hidden");
    assert(restored.length === 1 && !button(t.restore).disabled, "failed restore selected project or blocked retry");

    const before = calls.length;
    render("project-C", "http://192.0.2.1:8000"); await tick(); await tick();
    assert(fileInput().disabled && button(t.prepare).disabled && calls.length === before,
      "remote endpoint received private archive metadata or upload");
    assert(host.textContent?.includes(t.local), "remote operator restriction unexplained");
    return "private project package actual React: empty-history File restore, native attachment, discard, overlap, deferred JSON/scope cleanup, reload lease, collision retry, no LAN upload";
  } finally {
    flushSync(() => root.unmount()); host.remove(); window.fetch = originalFetch;
  }
}
