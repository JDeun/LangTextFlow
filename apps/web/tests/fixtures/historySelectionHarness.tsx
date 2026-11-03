import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { SessionHistory } from "../../src/SessionHistory";
import { LocaleProvider } from "../../src/i18n";
import { LOCALE_STORAGE_KEY } from "../../src/locales";
import type { EventProject, SessionDetail } from "../../src/types";

export async function runHistorySelectionChecks() {
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 150; i++) await tick();
    assert(check(), message);
  };
  const originalFetch = window.fetch;
  const oldLocale = localStorage.getItem(LOCALE_STORAGE_KEY);
  localStorage.setItem(LOCALE_STORAGE_KEY, "en");
  const sessions = ["a", "b"].map(id => ({
    session_id: id, project_id: null, join_code: id, title: `Session ${id}`,
    notes: "", presenter: null, preset: "general", source_language: "ko",
    target_languages: ["en"], engine: "mock", correction_provider: "none",
    correction_model: null, translation_provider: "none", translation_model: null,
    started_at: "2026-01-01T00:00:00Z", ended_at: "2026-01-01T01:00:00Z",
    interrupted: false, segment_count: 1,
    context: { description: "", hotwords: [], reference_documents: [] },
  } as SessionDetail));
  const projects = [{ project_id: "project-one", title: "Project one" } as EventProject];
  let checks = 0;
  try {
    for (const mutation of ["save", "assign", "delete"] as const) {
      for (const boundary of mutation === "delete" ? ["fetch"] : ["fetch", "json"]) {
        for (const action of ["stay", "other", "reopen", "close", "project", "api"] as const) {
          const host = document.createElement("div");
          document.body.append(host);
          const root = createRoot(host);
          let apiUrl = "";
          let projectId = "all";
          let release: (() => void) | null = null;
          let mutationStarted = false;
          let deleted = false;
          const updated = { ...sessions[0], title: "Session a updated", project_id: "project-one" };
          const label = `${mutation}/${boundary}/${action}`;
          window.fetch = async (input, init) => {
            const path = new URL(String(input), location.href).pathname.replace(/^\/replacement/, "");
            const method = init?.method ?? "GET";
            if (method !== "GET") {
              assert(!mutationStarted, `${label}: duplicate mutation`);
              mutationStarted = true;
              if (boundary === "json") return {
                ok: true, json: () => new Promise(resolve => { release = () => resolve(updated); }),
              } as Response;
              return new Promise<Response>(resolve => {
                release = () => { deleted = mutation === "delete"; resolve(Response.json(updated)); };
              });
            }
            if (path === "/api/v1/history") return Response.json(deleted ? sessions.slice(1) : sessions);
            if (path.endsWith("/captions")) {
              const id = path.split("/")[4];
              return Response.json([{
                segment_id: `source-${id}`, version: 1, committed: true, stage: "committed",
                start_ms: 0, end_ms: 1000, text: `Source ${id}`, original_text: `Source ${id}`,
                source_language: "ko", translations: {},
              }]);
            }
            if (path.endsWith("/archive")) return Response.json({
              recordings: [], summary: null, summary_stale: false,
              summary_external_providers: ["openai-compatible"],
            });
            if (path.endsWith("/transcripts")) return Response.json({ versions: [] });
            const session = sessions.find(item => path === `/api/v1/history/${item.session_id}`);
            if (session) return Response.json(session);
            throw new Error(`${label}: unexpected request ${method} ${path}`);
          };
          const render = () => flushSync(() => root.render(<LocaleProvider><SessionHistory
            apiUrl={apiUrl} targetLanguage="ko" activeSessionId={null} refreshToken="fixture"
            projects={projects} selectedProjectId={projectId}
            onSelectProject={id => { projectId = id; render(); }}
            onProjectsChanged={async () => undefined} /></LocaleProvider>));
          const detailTitle = () => host.querySelector(".history-detail-header h3")?.textContent;
          const row = (id: string) => Array.from(host.querySelectorAll<HTMLElement>(".history-item"))
            .find(element => element.querySelector("strong")?.textContent === `Session ${id}`)!;
          const open = async (id: string) => {
            row(id).querySelector<HTMLButtonElement>(".history-open")!.click();
            await until(() => detailTitle() === `Session ${id}`
              && !row(id).querySelector<HTMLButtonElement>(".history-open")!.disabled,
            `${label}: session ${id} did not open`);
          };
          try {
            render();
            await until(() => !!row("a"), `${label}: history did not load`);
            await open("a");
            if (mutation === "save") {
              host.querySelector<HTMLButtonElement>(".history-metadata-actions button")!.click();
            } else if (mutation === "assign") {
              const select = row("a").querySelector<HTMLSelectElement>("select")!;
              select.value = "project-one";
              select.dispatchEvent(new Event("change", { bubbles: true }));
            } else {
              row("a").querySelector<HTMLButtonElement>(".danger-quiet")!.click();
            }
            await until(() => !!release, `${label}: mutation did not reach deferred ${boundary}`);
            if (action === "other") await open("b");
            if (action === "reopen") await open("a");
            if (action === "close") host.querySelector<HTMLButtonElement>(".history-detail-header button")!.click();
            if (action === "project") { projectId = "unassigned"; render(); }
            if (action === "api") { apiUrl = "/replacement"; render(); }
            await tick();
            release!();
            // Allow the actual handler's await, state update, and passive effects to finish.
            await new Promise(resolve => setTimeout(resolve, 25));
            const expected = action === "other" ? "Session b"
              : action === "reopen" ? "Session a"
              : action === "stay" && mutation !== "delete" ? "Session a updated" : undefined;
            assert(detailTitle() === expected,
              `${label}: late response changed detail to ${detailTitle()} instead of ${expected}`);
            if (action === "other") assert(host.querySelector(".history-transcript-list")?.textContent?.includes("Source b"),
              `${label}: detail title and source transcript diverged`);
            checks++;
          } finally {
            flushSync(() => root.unmount());
            host.remove();
          }
        }
      }
    }
    return `${checks} deferred save/project/delete cases: current result, other/same detail, close, project and API scope`;
  } finally {
    window.fetch = originalFetch;
    if (oldLocale === null) localStorage.removeItem(LOCALE_STORAGE_KEY);
    else localStorage.setItem(LOCALE_STORAGE_KEY, oldLocale);
  }
}
