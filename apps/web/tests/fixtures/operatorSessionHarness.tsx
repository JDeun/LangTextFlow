import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { AudioCaptureController } from "../../src/audioCapture";
import type { AsrRegistry } from "../../src/asrCapabilities";
import { LocaleProvider } from "../../src/i18n";
import type { SessionState } from "../../src/types";
import { useOperatorSession } from "../../src/useOperatorSession";

// Exercise the actual React hook without real microphones, models, or server sessions.
export async function runOperatorSessionChecks() {
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let attempts = 0; !check() && attempts < 100; attempts++) await tick();
    assert(check(), message);
  };
  const originalFetch = window.fetch;
  const originalStart = AudioCaptureController.prototype.start;
  const originalStop = AudioCaptureController.prototype.stop;
  const requests: string[] = [];
  const captures: string[] = [];
  let mode: "ready" | "preflight-blocked" | "rejected" | "audio-failed" = "ready";
  let operator!: ReturnType<typeof useOperatorSession>;
  let lastPayload: Record<string, unknown> = {};
  let connected = true;
  const hydrated: SessionState[] = [];
  const session: SessionState = {
    session_id: "owned-session", join_code: "OWNED", running: true,
    source_language: "en", target_languages: ["ko", "ja"], engine: "faster-whisper",
    context: null, audio_required: true, audio_sample_rate: 16000,
    recording_enabled: false, recording_error: null, collect_audience_metrics: true,
    correction_status: { enabled: false, provider: "none", model: null, available: true, error: null },
    translation_status: { enabled: false, provider: "none", model: null, available: true, error: null },
    persistence_error: null, started_at: null,
  };
  let serverState: unknown = { ...session, session_id: null, running: false };
  const settings: Parameters<typeof operator.start>[0] = {
    selectedProjectId: "all", sourceLanguage: "en", targetLanguages: ["ko", "ja"],
    engine: "faster-whisper", resourceProfile: "low", correctionProvider: "none",
    correctionModel: "", translationProvider: "none", translationModel: "",
    title: "Session regression", presenter: "", preset: "church", hotwords: "Grace",
    referenceDocuments: [], displaySettings: {
      font_family: "system", font_scale_percent: 100, max_lines: 2,
      hold_seconds: 8, show_source_when_translated: true, text_align: "center",
    },
  };
  const providers: Parameters<typeof operator.start>[1] = {
    asr: { status: "ready", providers: [{
      provider: "faster-whisper", declared_languages: ["en"],
    }] } as AsrRegistry,
    translation: { status: "ready", providers: [] },
  };
  window.fetch = async (input, init) => {
    const path = new URL(String(input), location.href).pathname;
    requests.push(path);
    if (path === "/api/v1/state") return Response.json(serverState);
    if (path === "/api/v1/preflight") return Response.json({
      ready: mode !== "preflight-blocked", blocking_checks: ["asr-model"],
      checks: [{ id: "asr-model", label: "Install ASR model" }],
    });
    if (path === "/api/v1/session/start") {
      lastPayload = JSON.parse(String(init?.body));
      if (mode === "rejected") return new Response("session already running", { status: 409 });
      serverState = session;
      return Response.json(session);
    }
    if (path === "/api/v1/session/stop") {
      serverState = { ...session, running: false };
      return Response.json(serverState);
    }
    throw new Error(`Unexpected regression-test HTTP request: ${path}`);
  };
  AudioCaptureController.prototype.start = async () => {
    captures.push("start");
    if (mode === "audio-failed") throw new Error("Microphone unavailable");
    return 16000;
  };
  AudioCaptureController.prototype.stop = async () => { captures.push("stop"); };
  function Harness() {
    operator = useOperatorSession(connected, next => hydrated.push(next));
    return null;
  }
  let root = createRoot(document.getElementById("root")!);
  let mounted = true;
  const reset = () => { requests.length = 0; captures.length = 0; };
  const consent = () => flushSync(() => {
    operator.setRecordAudio(true);
    operator.setCollectAudienceMetrics(true);
  });
  const render = () => flushSync(() => root.render(<LocaleProvider><Harness /></LocaleProvider>));
  const mount = async () => {
    root = createRoot(document.getElementById("root")!);
    mounted = true;
    render();
    await tick();
    flushSync(() => operator.setDeviceId("test-microphone"));
  };
  const reconnect = async () => {
    connected = false; render(); await tick();
    connected = true; render(); await tick();
  };
  try {
    flushSync(() => root.render(<LocaleProvider><Harness /></LocaleProvider>));
    await tick();
    flushSync(() => operator.setDeviceId("test-microphone"));

    mode = "preflight-blocked";
    assert(await operator.start(settings, providers) === null, "blocked preflight started a session");
    await tick();
    assert(!requests.includes("/api/v1/session/start"), "blocked preflight posted session start");
    assert(!requests.includes("/api/v1/session/stop"), "blocked preflight stopped another session");
    assert(!operator.busy && operator.error.includes("Install ASR model"), "preflight error was lost");

    reset();
    mode = "rejected";
    assert(await operator.start(settings, providers) === null, "HTTP rejection produced a session");
    await tick();
    assert(!requests.includes("/api/v1/session/stop"), "HTTP rejection stopped another tab's session");
    assert(captures.length === 0, "HTTP rejection opened a microphone");

    reset();
    serverState = { ...session, session_id: "another-tab" };
    await operator.start(settings, providers);
    await until(() => operator.session?.session_id === "another-tab",
      "409 start rejection did not recover the already running session");
    assert(operator.running && captures.length === 0
      && !requests.includes("/api/v1/session/stop"), "409 recovery affected another tab's audio");
    await operator.stop(); await tick();

    reset();
    mode = "audio-failed";
    consent();
    assert(await operator.start(settings, providers) === null, "microphone failure retained session");
    await tick();
    assert(requests.filter((path) => path === "/api/v1/session/stop").length === 1,
      "acknowledged start did not roll back remote session exactly once");
    assert(captures.join(",") === "start,stop", "microphone failure did not clean capture");
    assert(!operator.session && !operator.recordAudio && !operator.collectAudienceMetrics,
      "failed session leaked state or consent");
    assert(operator.error === "Microphone unavailable" && !operator.busy, "original start error was lost");

    reset();
    mode = "ready";
    consent();
    await operator.start(settings, providers);
    await tick();
    assert(lastPayload.record_audio === true && lastPayload.collect_audience_metrics === true,
      "explicit per-session consent was not forwarded");
    assert(lastPayload.source_language === "en" && JSON.stringify(lastPayload.target_languages) === '["ko","ja"]',
      "session lifecycle changed the source or independent targets");
    await operator.stop();
    await tick();
    assert(!operator.running && !operator.recordAudio && !operator.collectAudienceMetrics,
      "stop carried consent to next session");
    assert(captures.join(",") === "start,stop", "normal stop did not close microphone");

    reset();
    await operator.start(settings, providers);
    await tick();
    assert(lastPayload.record_audio === false && lastPayload.collect_audience_metrics === false,
      "second session reused previous consent");
    flushSync(() => root.unmount());
    mounted = false;
    await tick();
    assert(captures.join(",") === "start,stop", "leaving operator route leaked microphone");
    assert(!requests.includes("/api/v1/session/stop"), "unmount stopped a server session implicitly");
    const baseFetch = window.fetch;
    for (const boundary of ["preflight", "ack"] as const) {
      reset();
      root = createRoot(document.getElementById("root")!);
      mounted = true;
      flushSync(() => root.render(<LocaleProvider><Harness /></LocaleProvider>));
      flushSync(() => operator.setDeviceId("test-microphone"));
      let release: ((response: Response) => void) | undefined;
      window.fetch = async (input, init) => {
        const path = new URL(String(input), location.href).pathname;
        if (path === (boundary === "preflight" ? "/api/v1/preflight" : "/api/v1/session/start")) {
          requests.push(path);
          return new Promise<Response>((resolve) => { release = resolve; });
        }
        return baseFetch(input, init);
      };
      const starting = operator.start(settings, providers);
      for (let attempts = 0; !release && attempts < 100; attempts++) await tick();
      assert(release, `start did not reach deferred ${boundary}`);
      flushSync(() => root.unmount());
      mounted = false;
      release!(Response.json(boundary === "preflight" ? { ready: true } : session));
      assert(await starting === null, `late ${boundary} restored an unmounted operator`);
      await tick();
      assert(captures.length === 0, `late ${boundary} opened a microphone after unmount`);
      assert(!requests.includes("/api/v1/session/stop"), `unmount at ${boundary} stopped a remote session`);
      if (boundary === "preflight") assert(!requests.includes("/api/v1/session/start"),
        "late preflight posted a session after unmount");
      window.fetch = baseFetch;
    }

    reset();
    hydrated.length = 0;
    serverState = { ...session, project_id: "recovered-project", resource_profile: "low",
      recording_enabled: true };
    await mount();
    await until(() => operator.running && operator.recoveredAudio,
      "reload did not recover remote running session");
    assert(operator.running && operator.recoveredAudio, "reload did not recover remote running session");
    assert(operator.session?.project_id === "recovered-project"
      && operator.session?.resource_profile === "low", "recovery lost server configuration");
    assert(!operator.recordAudio && !operator.collectAudienceMetrics && captures.length === 0,
      "reload replayed recording/metrics consent or opened microphone");
    assert(!requests.includes("/api/v1/session/stop"), "state recovery stopped a remote session");
    await operator.stop(); await tick();
    assert(!operator.running, "recovered session could not be explicitly stopped");

    reset();
    consent();
    await operator.start(settings, providers); await tick();
    await reconnect();
    assert(captures.join(",") === "start" && !operator.recoveredAudio,
      "same-session reconnect interrupted this tab's microphone");
    serverState = { ...session, session_id: null, running: false };
    requests.length = 0;
    await reconnect();
    await until(() => !operator.running, "backend restart state did not settle");
    assert(!operator.running && !operator.recordAudio && !operator.collectAudienceMetrics,
      "backend restart did not clear running controls and consent");
    assert(captures.join(",") === "start,stop", "backend restart leaked local capture");
    assert(!requests.includes("/api/v1/session/stop"), "backend restart read posted a remote stop");

    reset();
    await operator.start(settings, providers); await tick();
    serverState = { running: false, source_language: 123 };
    await reconnect();
    assert(operator.running && captures.join(",") === "start",
      "malformed state replaced controls or stopped the microphone");
    serverState = { ...session, session_id: "different-server-session" };
    requests.length = 0;
    await reconnect();
    await until(() => operator.session?.session_id === "different-server-session",
      "different-session reconnect did not settle");
    assert(captures.join(",") === "start,stop" && operator.recoveredAudio,
      "different session reused or reopened the previous microphone");
    assert(!requests.includes("/api/v1/session/stop"), "different-session hydration stopped the server");
    await operator.stop(); await tick();
    flushSync(() => root.unmount()); mounted = false;

    // A user start/stop after the request wins over both deferred HTTP and JSON bodies.
    for (const boundary of ["fetch", "json"] as const) {
      for (const action of ["start", "stop", "unmount"] as const) {
        reset(); hydrated.length = 0;
        let release: ((response: Response | SessionState) => void) | undefined;
        window.fetch = async (input, init) => {
          const path = new URL(String(input), location.href).pathname;
          if (path !== "/api/v1/state") return baseFetch(input, init);
          requests.push(path);
          if (boundary === "fetch") return new Promise<Response>((resolve) => {
            release = response => resolve(response as Response);
          });
          const response = Response.json(session);
          response.json = () => new Promise<SessionState>((resolve) => {
            release = next => resolve(next as SessionState);
          });
          return response;
        };
        await mount();
        for (let attempts = 0; !release && attempts < 100; attempts++) await tick();
        assert(release, `hydration did not reach deferred ${boundary}`);
        if (action === "unmount") {
          flushSync(() => root.unmount()); mounted = false;
        } else if (action === "start") {
          await operator.start(settings, providers);
        } else {
          await operator.stop();
        }
        const stale = { ...session, session_id: "stale-session" };
        release!(boundary === "fetch" ? Response.json(stale) : stale);
        await tick();
        assert(!hydrated.some(next => next.session_id === "stale-session"),
          `late ${boundary} restored a stale form after ${action}`);
        if (action === "start") assert(operator.session?.session_id === "owned-session",
          `late ${boundary} overwrote the new session`);
        if (action === "stop") assert(!operator.running,
          `late ${boundary} resurrected a stopped session`);
        if (mounted) { flushSync(() => root.unmount()); mounted = false; }
        window.fetch = baseFetch;
      }
    }
    return "start/stop ownership, consent, reload/reconnect hydration, capture cleanup, deferred state/action/unmount fences";
  } finally {
    if (mounted) flushSync(() => root.unmount());
    window.fetch = originalFetch;
    AudioCaptureController.prototype.start = originalStart;
    AudioCaptureController.prototype.stop = originalStop;
  }
}
