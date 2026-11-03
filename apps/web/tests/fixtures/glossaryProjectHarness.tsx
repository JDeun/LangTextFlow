import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import App from "../../src/App";
import { LocaleProvider } from "../../src/i18n";

// Actual workspace controls: project scope must not expose a global-write action.
export async function runGlossaryProjectChecks() {
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 100; i++) {
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    assert(check(), message);
  };
  const originalFetch = window.fetch;
  const originalSocket = window.WebSocket;
  const oldHash = location.hash;
  const onboardingKey = "langtextflow:onboarding:v1";
  const oldOnboarding = localStorage.getItem(onboardingKey);
  localStorage.setItem(onboardingKey, "complete");
  history.replaceState({}, "", `${location.pathname}${location.search}#/glossary`);
  const writes: { path: string; body: { term: string } }[] = [];
  window.WebSocket = class { close() {} } as unknown as typeof WebSocket;
  window.fetch = async (input, init) => {
    const path = new URL(String(input), location.href).pathname;
    if (init?.method === "POST") {
      writes.push({ path, body: JSON.parse(String(init.body)) });
      return Response.json({}, { status: 201 });
    }
    if (path === "/api/v1/projects") return Response.json([
      { project_id: "project-a", title: "Synthetic project A" },
    ]);
    if (path.endsWith("/recommendations")) return Response.json([
      { term: "Synthetic suggested term", occurrences: 3, sessions: 2, score: 1 },
    ]);
    if (path.endsWith("/glossary")) return Response.json([]);
    // Unrelated operator discovery is unavailable; no backend or microphone starts.
    return new Response("Fixture discovery unavailable", { status: 503 });
  };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const recommendations = () => host.querySelector('[data-testid="glossary-recommendations"]');
  const chooseScope = (value: string) => {
    const select = host.querySelector<HTMLSelectElement>('[data-testid="glossary-project-scope"]')!;
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  };
  try {
    flushSync(() => root.render(<LocaleProvider><App /></LocaleProvider>));
    await until(() => !!host.querySelector('option[value="project-a"]')
      && !!recommendations()?.querySelector(".recommendation-row"), "global recommendations did not load");
    assert(writes.length === 0, "loading global recommendations mutated terminology");
    chooseScope("project-a");
    await until(() => !recommendations(), "project scope exposed a global glossary Add action");
    assert(!!host.querySelector('[data-testid="glossary-manager"]') && writes.length === 0,
      "project scope lost its editor or changed the global glossary");
    chooseScope("global");
    await until(() => !!recommendations()?.querySelector(".recommendation-row"),
      "returning to global scope lost recommendations");
    recommendations()!.querySelector<HTMLButtonElement>(".recommendation-row button")!.click();
    await until(() => writes.length === 1, "explicit global Add did not save");
    assert(writes[0].path === "/api/v1/glossary"
      && writes[0].body.term === "Synthetic suggested term", "global Add changed its target or content");
    return "actual App global/project/global scope, project editor preserved, no silent global writes";
  } finally {
    flushSync(() => root.unmount());
    host.remove();
    window.fetch = originalFetch;
    window.WebSocket = originalSocket;
    history.replaceState({}, "", `${location.pathname}${location.search}${oldHash}`);
    if (oldOnboarding === null) localStorage.removeItem(onboardingKey);
    else localStorage.setItem(onboardingKey, oldOnboarding);
  }
}
