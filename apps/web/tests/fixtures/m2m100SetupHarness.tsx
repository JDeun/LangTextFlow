import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { LocaleProvider } from "../../src/i18n";
import { RuntimeMaintenancePanel } from "../../src/RuntimeMaintenancePanel";
import { TranslationProviderSelect } from "../../src/TranslationProviderSelect";
import { useTranslationCapabilities } from "../../src/useTranslationCapabilities";

// Actual UI/discovery wiring; network jobs are synthetic, no weights are requested.
export async function runM2M100SetupChecks() {
  const originalFetch = window.fetch;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const requests: string[] = [];
  let mode: "cancel" | "complete" = "cancel";
  let configured = false;
  let job = {
    job_id: "native-owned", provider: "m2m100-ct2", model: "facebook/m2m100_418M",
    state: "running", status: "Preparing verified files", progress_percent: null,
    completed_bytes: null, total_bytes: null, error: null,
  };
  const assert = (value: unknown, message: string) => { if (!value) throw new Error(message); };
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 200; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert(check(), message);
  };
  window.fetch = async (input, init) => {
    const path = new URL(String(input), location.href).pathname;
    requests.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/v1/translation/providers") return Response.json([
      { provider: "none", label: "Source", local_only: true, requires_model: false,
        configured: true, supported_pairs: [], field_qualified: false,
        glossary_mode: "unsupported", model_id: null, warnings: [] },
      { provider: "m2m100-ct2", label: "M2M100", local_only: true, requires_model: true,
        configured, supported_pairs: [["ko", "en"]], field_qualified: false,
        glossary_mode: "unsupported", model_id: "facebook/m2m100_418M", warnings: [] },
    ]);
    if (path === "/api/v1/setup/runtimes" || path === "/api/v1/setup/runtime-jobs") return Response.json([]);
    if (path === "/api/v1/setup/cache") return Response.json({
      root: "managed", entries: [], total_bytes: 0, writable_bytes: 1e9, safety_reserve_bytes: 1e8,
    });
    if (path === "/api/v1/setup/m2m100/prepare") {
      assert(init?.method === "POST", "prepare must be explicit POST");
      job = { ...job, state: "running" };
      return Response.json(job, { status: 202 });
    }
    if (path.endsWith("/cancel")) {
      job = { ...job, state: "cancelled" };
      return Response.json(job);
    }
    if (path === "/api/v1/setup/jobs/native-owned") {
      if (mode === "complete") { configured = true; job = { ...job, state: "completed" }; }
      return Response.json(job);
    }
    throw new Error(`Unexpected setup request: ${path}`);
  };
  function Harness({ disabled = false }: { disabled?: boolean }) {
    const [revision, setRevision] = useState(0);
    const registry = useTranslationCapabilities(revision);
    return <>
      <TranslationProviderSelect value="none" onChange={() => {}} sourceLanguage="ko"
        targetLanguages={["en"]} engine="faster-whisper" registry={registry} />
      <RuntimeMaintenancePanel disabled={disabled}
        onModelsChanged={() => setRevision(value => value + 1)} />
    </>;
  }
  const card = () => host.querySelector('[data-testid="m2m100-model-setup"]')!;
  const option = () => host.querySelector<HTMLOptionElement>('option[value="m2m100-ct2"]');
  try {
    flushSync(() => root.render(<LocaleProvider><Harness /></LocaleProvider>));
    await until(() => option() !== null, "initial provider discovery");
    assert(option()?.disabled, "missing native cache is disabled");
    assert(!requests.some(path => path.includes("/prepare")), "render must not download");
    (card().querySelector("button") as HTMLButtonElement).click();
    await until(() => !!card().querySelector(".model-setup-job.running"), "job progress rendered");
    (card().querySelector("button") as HTMLButtonElement).click();
    await until(() => !!card().querySelector(".model-setup-job.cancelled"), "cancel request rendered");
    (card().querySelector("button") as HTMLButtonElement).click();
    await until(() => !card().querySelector(".model-setup-job"), "retry reset");
    mode = "complete";
    (card().querySelector("button") as HTMLButtonElement).click();
    await until(() => option()?.disabled === false, "completed setup refreshes provider availability");
    assert((host.querySelector("select") as HTMLSelectElement).value === "none", "no model promotion");
    assert(requests.filter(path => path === "POST /api/v1/setup/m2m100/prepare").length === 2,
      "only explicit prepare actions submit jobs");
    flushSync(() => root.render(<LocaleProvider><Harness disabled /></LocaleProvider>));
    assert((card().querySelector("button") as HTMLButtonElement).disabled,
      "active caption session disables repeat preparation");
    return "explicit prepare, progress, cancel/retry, capability refresh, no default change";
  } finally {
    flushSync(() => root.unmount());
    host.remove();
    window.fetch = originalFetch;
  }
}
