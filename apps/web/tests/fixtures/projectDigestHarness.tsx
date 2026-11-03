import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { ProjectDigestPanel } from "../../src/ProjectDigestPanel";
import { PROJECT_DIGEST_COPY } from "../../src/projectDigestCopy";
import { POST_EVENT_COPY } from "../../src/postEventCopy";

export async function runProjectDigestChecks() {
  const t = PROJECT_DIGEST_COPY.en, c = POST_EVENT_COPY.en;
  const assert = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 100; i++) await tick();
    assert(check(), message);
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const originalFetch = window.fetch;
  const posts: { path: string; body: Record<string, unknown> }[] = [];
  const opened: string[] = [];
  const modelDraft = "Chronology [p0001.s000001 at 1s] [p0002.s000001 at 2s] [p0003.s000001 at 3s]";
  const base = {
    digest_id: "project-draft", status: "completed", language: "en", provider: "ollama", model: "fixture-only",
    prompt_version: "project-v1", input_sha256: "a".repeat(64), created_at: "2026-11-03T12:00:00Z",
    stale: false, error: null, review: null,
    content: modelDraft, evidence_available: true,
    snapshot: {
      source: "[p0001.s000001 at 1s] {} Original frozen evidence",
      sessions: [1, 2, 3].map(n => ({ session_id: `session-${n}`, title: `Source ${n}`, transcript_id: null })),
      citations: [1, 2, 3].map(n => ({ alias: `p000${n}.s000001`, marker: `[p000${n}.s000001 at ${n}s]`,
        session_id: `session-${n}`, segment_id: "same-segment", start_ms: n * 1000, transcript_id: null })),
    },
  };
  let versions: Record<string, unknown>[] = [];
  let record = structuredClone(base) as Record<string, unknown>;
  let policy: unknown = undefined;
  let malformed = false;
  let stale = false;
  let generatedRunning = false;
  let delay: { promise: Promise<Response>; resolve: (value: Response) => void } | null = null;
  const listing = () => ({
    versions: malformed ? [null] : versions,
    input: { input_sha256: "a".repeat(64), session_count: 3, excluded_running_count: 1, interrupted_count: 1 },
    input_error: null, summary_external_providers: policy,
  });
  window.fetch = async (input, init) => {
    const path = new URL(String(input), location.href).pathname;
    if (init?.method === "POST") {
      const body = init.body ? JSON.parse(String(init.body)) : {};
      posts.push({ path, body });
      if (path.endsWith("/reviews")) record = { ...record, review: { review_id: `review-${posts.length}`, ...body } };
      else if (path.endsWith("/cancel")) record = { ...record, status: "error", error: "Cancelled" };
      else record = { ...structuredClone(base), status: generatedRunning ? "running" : "completed", content: generatedRunning ? "" : modelDraft };
      versions = [record];
      return Response.json(record, { status: 202 });
    }
    if (path.endsWith("/digests")) {
      if (delay) { const pending = delay; delay = null; return pending.promise; }
      return Response.json(listing());
    }
    return Response.json({ ...record, stale });
  };
  const button = (label: string) => Array.from(host.querySelectorAll("button")).find(el => el.textContent === label)!;
  const render = (projectId = "project-A", apiUrl = "") => flushSync(() => root.render(
    <ProjectDigestPanel apiUrl={apiUrl} projectId={projectId} locale="en" onOpenSession={sid => opened.push(sid)} />,
  ));
  const select = (label: string) => Array.from(host.querySelectorAll("label")).find(el => el.textContent?.startsWith(label))!.querySelector("select")!;
  const change = (element: HTMLSelectElement, value: string) => { element.value = value; element.dispatchEvent(new Event("change", { bubbles: true })); };
  const checkbox = () => host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  const textarea = () => host.querySelector<HTMLTextAreaElement>("textarea");
  const refresh = async () => { button(c.refresh).click(); await tick(); await tick(); };
  try {
    render();
    await until(() => host.textContent?.includes(c.providerPolicyUnknown) ?? false, "missing provider policy not shown");
    assert(button(t.generate).disabled && posts.length === 0, "unknown policy allowed project transmission");
    policy = ["openai-compatible", "ollama"];
    await refresh();
    await until(() => !!checkbox(), "remote policy did not recover");
    assert(button(t.generate).disabled, "remote Ollama did not require explicit consent");
    checkbox()!.click(); await tick();
    change(select(c.summaryProvider), "openai-compatible"); await tick();
    assert(!checkbox()!.checked && button(t.generate).disabled, "provider change reused approval");
    change(select(c.summaryProvider), "ollama"); await tick();
    checkbox()!.click(); await tick();
    button(t.generate).click();
    await until(() => textarea()?.value === modelDraft, "fast completion did not populate actual review textarea");
    assert(posts[0].body.allow_external_provider === true && posts[0].path.includes("project-A"), "explicit remote generation body/scope incorrect");
    button(c.saveReview).click();
    await until(() => !!record.review && !button(c.approveReview).disabled, "saved project review not refreshed");
    await tick(); await tick();
    await until(() => !button(c.approveReview).disabled, "project review save still pending");
    assert(posts.at(-1)!.body.expected_review_id === null, "first review guessed a prior identity");
    button(c.approveReview).click();
    await until(() => textarea()?.readOnly === true, `approved project version not locked: ${JSON.stringify(posts)}`);
    assert(typeof posts.at(-1)!.body.expected_review_id === "string", "approval omitted saved review concurrency identity");
    const sources = Array.from(host.querySelectorAll("button")).filter(el => el.textContent?.startsWith(t.source));
    assert(sources.length === 3, "cross-session source citations missing");
    sources[1].click(); assert(opened[0] === "session-2", "same segment ID linked wrong session");
    assert(host.textContent?.includes("Original frozen evidence"), "frozen original evidence unavailable");

    stale = true; await refresh();
    assert(button(c.saveReview).disabled && host.textContent?.includes(t.stale), "stale source allowed review");
    stale = false; record = { ...record, evidence_available: false, content: "", snapshot: null, review: null }; await refresh();
    assert(!textarea() && host.textContent?.includes(t.unavailable), "moved source leaked through old project view");

    // Same ID transitions from running to completed; review text must follow content.
    policy = ["openai-compatible"]; versions = []; record = structuredClone(base);
    generatedRunning = true; render("project-B");
    await until(() => !button(t.generate).disabled, "local project did not load");
    assert(!checkbox(), "local Ollama required additional consent");
    button(t.generate).click(); await until(() => !!button(c.cancelSummary), "running project cancellation not available");
    assert(posts.at(-1)!.body.allow_external_provider === false, "local request forwarded external approval");
    record = { ...record, status: "completed", content: modelDraft }; versions = [record]; await refresh();
    await until(() => textarea()?.value === modelDraft, "same-ID completion kept blank review text");
    generatedRunning = true; versions = []; render("project-C");
    await until(() => !button(t.generate).disabled, "new project form did not reset");
    button(t.generate).click(); await until(() => !!button(c.cancelSummary), "second running job not shown");
    button(c.cancelSummary).click(); await until(() => record.status === "error" && !button(t.generate).disabled, "cancel did not allow retry");

    // Deferred JSON from an older project/endpoint must not install its policy or draft.
    let releaseJson!: (value: unknown) => void;
    const jsonPromise = new Promise(resolve => { releaseJson = resolve; });
    const response = new Response(); Object.defineProperty(response, "json", { value: () => jsonPromise });
    let resolveResponse!: (value: Response) => void;
    delay = { promise: new Promise(resolve => { resolveResponse = resolve; }), resolve: value => resolveResponse(value) };
    const held = delay;
    versions = []; render("old-project"); await tick(); held.resolve(response); await tick();
    render("new-project", "http://127.0.0.1:9999");
    await until(() => !button(t.generate).disabled, "new endpoint/project policy failed to load");
    releaseJson({ ...listing(), summary_external_providers: ["openai-compatible", "ollama"], versions: [base] });
    await tick(); await tick();
    assert(!checkbox() && !textarea() && host.textContent?.includes(t.none), "late old JSON replaced current project/policy");

    malformed = true; await refresh();
    assert(button(t.generate).disabled && host.textContent?.includes(c.failure), "malformed versions were trusted");
    malformed = false; await refresh(); await until(() => !button(t.generate).disabled, "refresh failed to recover malformed listing");
    return "project digest actual React: policy recovery/reset, Generate/review/approve, exact-session citations, stale/move, same-ID completion, cancel, scope/deferred-JSON fence and malformed recovery";
  } finally {
    flushSync(() => root.unmount()); host.remove(); window.fetch = originalFetch;
  }
}
