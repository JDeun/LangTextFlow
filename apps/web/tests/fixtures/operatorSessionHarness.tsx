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
  const originalFetch = window.fetch;
  const originalStart = AudioCaptureController.prototype.start;
  const originalStop = AudioCaptureController.prototype.stop;
  const requests: string[] = [];
  const captures: string[] = [];
  let mode: "ready" | "preflight-blocked" | "rejected" | "audio-failed" = "ready";
  let operator!: ReturnType<typeof useOperatorSession>;
  let lastPayload: Record<string, unknown> = {};
  const session: SessionState = {
    session_id: "owned-session", join_code: "OWNED", running: true,
    source_language: "en", target_languages: ["ko", "ja"], engine: "faster-whisper",
    context: null, audio_required: true, audio_sample_rate: 16000,
    recording_enabled: false, recording_error: null, collect_audience_metrics: true,
    correction_status: { enabled: false, provider: "none", model: null, available: true, error: null },
    translation_status: { enabled: false, provider: "none", model: null, available: true, error: null },
    persistence_error: null, started_at: null,
  };
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
    if (path === "/api/v1/preflight") return Response.json({
      ready: mode !== "preflight-blocked", blocking_checks: ["asr-model"],
      checks: [{ id: "asr-model", label: "Install ASR model" }],
    });
    if (path === "/api/v1/session/start") {
      lastPayload = JSON.parse(String(init?.body));
      if (mode === "rejected") return new Response("session already running", { status: 409 });
      return Response.json(session);
    }
    if (path === "/api/v1/session/stop") return Response.json({ ...session, running: false });
    throw new Error(`Unexpected regression-test HTTP request: ${path}`);
  };
  AudioCaptureController.prototype.start = async () => {
    captures.push("start");
    if (mode === "audio-failed") throw new Error("Microphone unavailable");
    return 16000;
  };
  AudioCaptureController.prototype.stop = async () => { captures.push("stop"); };
  function Harness() {
    operator = useOperatorSession();
    return null;
  }
  const root = createRoot(document.getElementById("root")!);
  let mounted = true;
  const reset = () => { requests.length = 0; captures.length = 0; };
  const consent = () => flushSync(() => {
    operator.setRecordAudio(true);
    operator.setCollectAudienceMetrics(true);
  });
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
    return "preflight/rejected-start ownership, microphone rollback, consent reset, unmount cleanup";
  } finally {
    if (mounted) flushSync(() => root.unmount());
    window.fetch = originalFetch;
    AudioCaptureController.prototype.start = originalStart;
    AudioCaptureController.prototype.stop = originalStop;
  }
}
