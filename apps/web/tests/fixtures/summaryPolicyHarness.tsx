import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { PostEventPanel } from "../../src/PostEventPanel";
import { POST_EVENT_COPY } from "../../src/postEventCopy";
import type { SessionDetail } from "../../src/types";

// Exercise real controls and fetch boundaries without sending any transcript externally.
export async function runSummaryPolicyChecks() {
  const copy = POST_EVENT_COPY.en;
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 100; i++) await tick();
    assert(check(), message);
  };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const originalFetch = window.fetch;
  const archive = {
    recordings: [], summary: null, summary_stale: false,
    approved_transcript_id: "approved-source",
  };
  let metadata: unknown = undefined;
  let storedSummary: unknown = null;
  let archiveFailed = false;
  let release: ((response: Response) => void) | null = null;
  const posts: Record<string, unknown>[] = [];
  window.fetch = async (input, init) => {
    const path = new URL(String(input), location.href).pathname;
    if (path.endsWith("/transcripts")) return Response.json({ versions: [] });
    if (path.endsWith("/summary")) {
      posts.push(JSON.parse(String(init?.body)));
      return Response.json({}, { status: 202 });
    }
    if (path.endsWith("/archive")) {
      if (archiveFailed) return new Response("Archive unavailable", { status: 503 });
      if (!release) return new Promise<Response>(resolve => { release = resolve; });
      return Response.json({ ...archive, summary: storedSummary, summary_external_providers: metadata });
    }
    throw new Error(`Unexpected request: ${path}`);
  };
  const button = (text: string) => Array.from(host.querySelectorAll("button"))
    .find(element => element.textContent === text)!;
  const generate = () => button(copy.generate);
  const checkbox = () => host.querySelector<HTMLInputElement>('input[type="checkbox"]');
  const provider = () => Array.from(host.querySelectorAll("label"))
    .find(element => element.textContent?.startsWith(copy.summaryProvider))!
    .querySelector<HTMLSelectElement>("select")!;
  const change = (select: HTMLSelectElement, value: string) => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const refresh = async () => {
    button(copy.refresh).click();
    await tick(); await tick();
  };
  try {
    flushSync(() => root.render(<PostEventPanel apiUrl="" locale="en" segments={[]}
      session={{ session_id: "summary-policy", source_language: "ko" } as SessionDetail}
      onOpenSegment={() => undefined} />));
    assert(generate().disabled, "pending archive policy enabled generation");
    await until(() => !!release, "archive request did not start");
    release!(Response.json(archive));
    await tick(); await tick();
    assert(generate().disabled && host.textContent?.includes(copy.providerPolicyUnknown),
      "missing metadata must fail closed with refresh recovery");
    generate().click();
    assert(posts.length === 0, "unverified policy sent a summary request");

    metadata = ["openai-compatible", "ollama"];
    await refresh();
    await until(() => !!checkbox(), "remote policy refresh did not render consent");
    assert(checkbox() && generate().disabled, "remote Ollama did not require consent");
    checkbox()!.click(); await tick();
    assert(!generate().disabled, "explicit remote approval did not enable generation");
    change(provider(), "openai-compatible"); await tick();
    assert(!checkbox()!.checked && generate().disabled, "provider change reused consent");
    checkbox()!.click(); await tick();
    change(provider(), "ollama"); await tick();
    assert(!checkbox()!.checked && generate().disabled, "returning to Ollama reused consent");
    checkbox()!.click(); await tick();
    change(host.querySelector<HTMLSelectElement>('[data-testid="summary-transcript-source"]')!, "approved-source");
    await tick();
    assert(!checkbox()!.checked && generate().disabled, "source change reused consent");
    checkbox()!.click(); await tick();
    generate().click();
    await until(() => posts.length === 1, "approved remote request was not sent");
    await tick(); await tick();
    assert(posts[0].provider === "ollama" && posts[0].allow_external_provider === true
      && posts[0].transcript_id === "approved-source", "remote request lost approval or selected source");
    assert(!checkbox()!.checked && generate().disabled, "approval leaked to the next request");

    metadata = ["openai-compatible"];
    await refresh();
    assert(!checkbox() && !generate().disabled, "loopback Ollama required extra consent");
    generate().click();
    await until(() => posts.length === 2, "local request was not sent");
    assert(posts[1].allow_external_provider === false, "local request included external approval");
    await tick(); await tick();
    metadata = ["ollama"];
    await refresh();
    assert(generate().disabled && posts.length === 2, "malformed policy enabled generation");
    metadata = ["openai-compatible"];
    archiveFailed = true;
    await refresh();
    assert(generate().disabled, "failed metadata refresh reused old approval policy");
    archiveFailed = false;
    await refresh();
    await until(() => !generate().disabled, "successful refresh did not recover after lookup failure");
    storedSummary = {
      summary_id: "historical-summary", status: "completed", provider: "openai-compatible",
      model: "recorded-model", language: "en", content: "Stored draft", review: null,
    };
    await refresh();
    await until(() => !!host.querySelector(".history-post-summary"), "historical summary did not render");
    assert(host.querySelector(".history-post-summary small")?.textContent?.includes("openai-compatible")
      && host.textContent?.includes(copy.endpointUnrecorded),
    "historical provider provenance was inferred from the current Ollama selection");
    const generatedSummary = {
      summary_id: "new-summary", status: "running", provider: "ollama",
      model: "recorded-model", language: "en", content: "", review: null,
    };
    storedSummary = generatedSummary;
    await refresh();
    await until(() => !host.querySelector(".history-post-summary"), "running summary still showed a review editor");
    storedSummary = { ...generatedSummary, status: "completed", content: "Generated draft" };
    await refresh();
    const editor = () => host.querySelector<HTMLTextAreaElement>(".history-post-summary textarea");
    await until(() => editor()?.value === "Generated draft", "same-ID completion did not populate review text");
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor(), "Unsaved review");
    editor()!.dispatchEvent(new Event("input", { bubbles: true }));
    await tick();
    await refresh();
    assert(editor()?.value === "Unsaved review", "unchanged archive refresh overwrote unsaved review");
    return "metadata fail-closed and recovery, remote approval, provider/source reset, local policy, historical provenance, same-ID completion and unsaved review preservation";
  } finally {
    flushSync(() => root.unmount());
    host.remove();
    window.fetch = originalFetch;
  }
}
