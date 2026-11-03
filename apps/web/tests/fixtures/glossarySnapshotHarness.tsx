import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import App from "../../src/App";
import { LocaleProvider } from "../../src/i18n";
import { useGlossarySnapshot } from "../../src/useGlossarySnapshot";
import type { GlossaryResolveRequest } from "../../src/glossarySnapshot";

export async function runGlossarySnapshotChecks() {
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 150; i++) await tick();
    assert(check(), message);
  };
  const originalFetch = window.fetch;
  const originalSocket = window.WebSocket;
  const oldHash = location.hash;
  const onboardingKey = "langtextflow:onboarding:v1";
  const oldOnboarding = localStorage.getItem(onboardingKey);
  const host = document.createElement("div");
  document.body.append(host);
  let root = createRoot(host);
  const digest = "a".repeat(64);
  const snapshot = (request: GlossaryResolveRequest, sha256 = digest) => ({
    source_language: request.source_language, preset: request.context.preset,
    project_id: request.project_id, sha256,
    entries: [{ term: "Synthetic resolved term", source_language: request.source_language,
      translations: { en: "Synthetic mapping" }, aliases: ["Synthetic alias"],
      enabled: true, presets: [], category: "synthetic", boost: 1 }],
  });
  let checks = 0;
  try {
    // Actual App -> preview -> useOperatorSession -> request builder. No server,
    // microphone or model starts; the synthetic start rejection tests reset too.
    localStorage.setItem(onboardingKey, "complete");
    history.replaceState({}, "", `${location.pathname}${location.search}`);
    window.WebSocket = class {
      onopen: (() => void) | null = null;
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      close() {}
    } as unknown as typeof WebSocket;
    const posts: Record<string, unknown>[] = [];
    window.fetch = async (input, init) => {
      const path = new URL(String(input), location.href).pathname;
      if (path === "/api/v1/asr/providers") return Response.json([{
        provider: "mock", label: "Synthetic test captions", implementation: "synthetic",
        emitted_stages: ["stable"], timestamp_origin: "synthetic", hotwords: "unsupported",
        word_timestamps: false, declared_languages: ["ko", "en", "ja"],
        model_language_quality_verified: false, native_session_cancellation_verified: false, warnings: [],
      }]);
      if (path === "/api/v1/translation/providers") return Response.json(["none", "demo"].map(provider => ({
        provider, label: provider, local_only: true, requires_model: false,
        supported_pairs: null, configured: true, field_qualified: false,
        glossary_mode: "unsupported", model_id: null, warnings: [],
      })));
      if (path === "/api/v1/projects") return Response.json([]);
      if (path === "/api/v1/glossary/resolve") {
        const request = JSON.parse(String(init?.body));
        assert(!("record_audio" in request) && !("reference_documents" in request.context)
          && !("translation_model" in request), "preview resent unrelated audio/provider/document settings");
        return Response.json(snapshot(request));
      }
      if (path === "/api/v1/session/start") {
        posts.push(JSON.parse(String(init?.body)));
        return new Response("glossary snapshot changed; review terminology again", { status: 409 });
      }
      if (path === "/api/v1/preflight") return Response.json({
        ready: true, blocking_checks: [], checks: [], architecture: "synthetic",
        memory_gb: null, disk_free_gb: null, translation_provider: "demo", translation_model: null,
        recommended: { engine: "mock", resource_profile: "standard", translation_provider: "demo",
          translation_model: null, reasons: [] },
      });
      return new Response("Fixture discovery unavailable", { status: 503 });
    };
    flushSync(() => root.render(<LocaleProvider><App /></LocaleProvider>));
    await until(() => !!host.querySelector('[data-testid="engine-select"] option[value="mock"]'),
      "synthetic provider discovery did not load");
    const engine = host.querySelector<HTMLSelectElement>('[data-testid="engine-select"]')!;
    engine.value = "mock";
    engine.dispatchEvent(new Event("change", { bubbles: true }));
    await until(() => !host.querySelector<HTMLButtonElement>('[data-testid="session-toggle"]')!.disabled,
      "synthetic caption connection did not open");
    host.querySelector<HTMLButtonElement>('[data-testid="glossary-snapshot-resolve"]')!.click();
    await until(() => !!host.querySelector('[data-testid="glossary-snapshot-result"]'),
      "actual App did not display resolved terminology");
    assert(host.textContent?.includes("Synthetic mapping") && host.textContent?.includes("Synthetic alias"),
      "review omitted the effective mapping or aliases");
    host.querySelector<HTMLButtonElement>('[data-testid="session-toggle"]')!.click();
    await until(() => posts.length === 1 && !host.querySelector('[data-testid="glossary-snapshot-result"]'),
      "start rejection failed to invalidate the reviewed snapshot");
    assert(posts[0].glossary_snapshot_sha256 === digest,
      "actual App/hook/start builder lost the reviewed digest");
    assert((posts[0].context as { glossary: unknown[] }).glossary.length === 0,
      "preview was promoted into explicit session overrides");
    checks++;
    flushSync(() => root.unmount());

    // Deliberately ignore AbortSignal in the mock: response and JSON fences,
    // not network cooperation, must protect the next visible scope.
    for (const boundary of ["fetch", "json", "failure"] as const) {
      for (const change of ["source", "preset", "project", "override", "api", "unmount"] as const) {
        root = createRoot(host);
        let request: GlossaryResolveRequest = {
          source_language: "ko", project_id: null, context: { preset: "church", glossary: [] },
        };
        let api = "";
        let controller!: ReturnType<typeof useGlossarySnapshot>;
        let release: (() => void) | null = null;
        let first = true;
        const label = `${boundary}/${change}`;
        const fresh = "b".repeat(64);
        window.fetch = async (_input, init) => {
          const captured = JSON.parse(String(init?.body)) as GlossaryResolveRequest;
          if (!first) return Response.json(snapshot(captured, fresh));
          first = false;
          if (boundary === "json") return {
            ok: true, json: () => new Promise(resolve => {
              release = () => resolve(snapshot(captured));
            }),
          } as Response;
          return new Promise<Response>(resolve => {
            release = () => resolve(boundary === "failure"
              ? new Response("Unavailable", { status: 503 }) : Response.json(snapshot(captured)));
          });
        };
        function Harness() {
          controller = useGlossarySnapshot(api, request, true);
          return <span>{controller.snapshot?.sha256 ?? "unreviewed"}</span>;
        }
        const render = () => flushSync(() => root.render(<Harness />));
        render();
        void controller.preview();
        await until(() => !!release, `${label}: old request did not reach boundary`);
        if (change === "source") request = { ...request, source_language: "en" };
        if (change === "preset") request = { ...request, context: { ...request.context, preset: "lecture" } };
        if (change === "project") request = { ...request, project_id: "other" };
        if (change === "override") request = { ...request, context: { ...request.context, glossary: [
          { term: "Explicit session term", source_language: "ko", translations: {}, aliases: [],
            presets: [], enabled: true, category: "general", boost: 1 },
        ] } };
        if (change === "api") api = "/replacement";
        if (change === "unmount") { flushSync(() => root.unmount()); root = createRoot(host); }
        render();
        assert(controller.snapshot === null, `${label}: old snapshot visible in new scope`);
        await controller.preview();
        await until(() => controller.snapshot?.sha256 === fresh, `${label}: new review did not load`);
        release!();
        await tick(); await tick();
        assert(controller.snapshot?.sha256 === fresh && !controller.failed,
          `${label}: old response/failure overwrote the new review`);
        flushSync(() => root.unmount());
        checks++;
      }
    }

    root = createRoot(host);
    let controller!: ReturnType<typeof useGlossarySnapshot>;
    const request: GlossaryResolveRequest = {
      source_language: "ko", project_id: null, context: { preset: "church", glossary: [] },
    };
    let malformed = false;
    let enabled = true;
    window.fetch = async () => Response.json(malformed ? { ...snapshot(request), project_id: "wrong" } : snapshot(request));
    function Harness() {
      controller = useGlossarySnapshot("", request, enabled);
      return null;
    }
    const render = () => flushSync(() => root.render(<Harness />));
    render();
    await controller.preview();
    await until(() => !!controller.snapshot, "valid current review missing");
    malformed = true;
    await controller.preview();
    await until(() => controller.failed, "malformed current response did not fail closed");
    assert(controller.snapshot === null, "failed refresh reused the prior digest");
    malformed = false;
    await controller.preview();
    await until(() => !!controller.snapshot, "retry did not recover current review");
    enabled = false; render();
    assert(controller.snapshot === null, "running session reused a pre-start review");
    checks++;
    return `${checks} actual App and deferred fetch/JSON/failure scope, override, API, unmount and malformed-response cases`;
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
