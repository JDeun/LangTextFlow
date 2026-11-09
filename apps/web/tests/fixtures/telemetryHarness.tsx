import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { LocaleProvider } from "../../src/i18n";
import { LOCALE_STORAGE_KEY, type Locale } from "../../src/locales";
import { TelemetryPanel } from "../../src/TelemetryPanel";
import type { RealtimeMetrics, RustAuthorityMetrics } from "../../src/types";
import { UTILITY_COPY } from "../../src/utilityCopy";
import "../../src/styles.css";
import "../../src/productShell.css";
import "../../src/accessibility.css";
import "../../src/layoutResilience.css";

let dispose = () => {};
export function disposeTelemetryChecks() { dispose(); }

// Actual React component, synthetic API only; no microphones or native models.
export async function runTelemetryChecks(locale: Locale) {
  dispose();
  const assert = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean) => {
    for (let i = 0; !check() && i < 100; i++) await tick();
    assert(check(), "native telemetry did not render its API sample");
  };
  const savedFetch = window.fetch;
  const savedLocale = localStorage.getItem(LOCALE_STORAGE_KEY);
  localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  const copy = UTILITY_COPY[locale].telemetry;
  const host = document.getElementById("root")!;
  const root = createRoot(host);
  const metrics: RealtimeMetrics = {
    runtime_core: "rust", audio_frames_received: 0, audio_bytes_received: 0, audio_duration_ms: 0,
    audio_rms_dbfs: null, voice_active: false, last_audio_enqueue_wait_ms: null,
    audio_backpressure_events: 0, asr_provider: "faster-whisper", asr_running: true, asr_failure: null,
    asr_failover_count: 0, asr_last_failover_reason: null, asr_last_failover_audio_ms: null,
    asr_queue_depth: null, asr_queue_capacity: null, asr_queue_high_watermark: null,
    persistence_queue_depth: null, persistence_queue_capacity: null, persistence_queue_high_watermark: null,
    persistence_events_dropped: null, persistence_events_failed: null,
    postprocess_queue_depth: null, postprocess_queue_capacity: null, postprocess_queue_high_watermark: null,
    postprocess_queue_lag_p50_ms: null, postprocess_queue_lag_p95_ms: null,
    postprocess_segments_dropped: null, postprocess_segments_failed: null, postprocess_segments_expired: null,
    postprocess_duplicate_asr_events_dropped: null, stale_asr_events_dropped: null,
    websocket_clients: 0, websocket_client_capacity: 8, websocket_clients_rejected: 0,
    websocket_overflow_evictions: 0, websocket_send_failures: 0, websocket_client_queue_high_watermark: 0,
    last_asr_lag_ms: null, last_correction_latency_ms: null, last_translation_latency_ms: null,
    last_commit_latency_ms: null, last_event_at: null,
  };
  const sample: NonNullable<RustAuthorityMetrics["sample"]> = {
    session_id: "owned-fixture", session_generation: 1, sequence: 1,
    sampled_at: "2026-11-10T00:00:00Z", final: false, stop_error: null,
    correction: { queued: 0, active: 0, capacity: 4, high: 1, admitted: 1, succeeded: 1,
      failed: 0, expired: 0, cancelled: 0, overflow: 0, unavailable: 0, wait_samples: 1,
      wait_p50_ms: 1.5, wait_p95_ms: 1.5 },
    translation: { queued: 0, active: 0, capacity: 4, high: 2, admitted: 2, succeeded: 1,
      failed: 1, expired: 0, cancelled: 0, overflow: 0, unavailable: 0, wait_samples: 2,
      wait_p50_ms: 4, wait_p95_ms: 6 },
  };
  window.fetch = async input => {
    const path = new URL(String(input), location.href).pathname;
    if (path === "/api/v1/metrics") return Response.json(metrics);
    if (path === "/api/v1/state") return Response.json({ session_id: "owned-fixture", persistence_error: null });
    if (path === "/api/v1/metrics/audience-render") return Response.json({ reporting_enabled: false, session_id: "owned-fixture", stages: {} });
    throw new Error(`Unexpected telemetry fixture request: ${path}`);
  };
  dispose = () => {
    flushSync(() => root.unmount());
    window.fetch = savedFetch;
    if (savedLocale === null) localStorage.removeItem(LOCALE_STORAGE_KEY);
    else localStorage.setItem(LOCALE_STORAGE_KEY, savedLocale);
    dispose = () => {};
  };
  try {
    for (const status of ["not_sampled", "stale", "unavailable", "fresh", "final"] as const) {
      const measured = status === "fresh" || status === "final";
      metrics.asr_running = status !== "final";
      metrics.rust_authority = { status, age_ms: status === "not_sampled" ? null : status === "fresh" ? 25 : 2000,
        sample: status === "not_sampled" ? null : { ...sample, final: status === "final" } };
      metrics.asr_queue_depth = measured ? 0 : null;
      metrics.asr_queue_capacity = measured ? 8 : null;
      metrics.asr_queue_high_watermark = measured ? 2 : null;
      metrics.postprocess_queue_depth = measured ? 0 : null;
      metrics.postprocess_queue_capacity = measured ? 10 : null;
      flushSync(() => root.render(<LocaleProvider><TelemetryPanel key={status} apiUrl="" running={status !== "final"} /></LocaleProvider>));
      const label = { not_sampled: copy.metricsNotSampled, stale: copy.metricsStale,
        unavailable: copy.metricsUnavailable, fresh: copy.metricsFresh, final: copy.metricsFinal }[status];
      await until(() => Boolean(host.querySelector("[data-testid='native-metrics-status']")?.textContent?.includes(label)));
      const post = host.querySelector(".telemetry-native-queues")!;
      assert(post.querySelector("strong")?.textContent === (measured ? "0 / 10" : "—"), "unknown queue became zero");
      assert(!post.textContent?.includes("p95 0 ms"), "unknown or separate populations became a zero global percentile");
      assert(post.querySelectorAll(".telemetry-lane").length === (measured ? 2 : 0), "stale job metrics appeared live");
      if (measured) assert(post.classList.contains("warning"), "failed target job did not warn");
      assert(host.scrollWidth <= host.clientWidth + 1, "native metrics overflow the shipped width");
      assert(!host.textContent?.includes("NaN"), "nullable metric rendered NaN");
    }
    // Keep only this owned fixture mounted until the runner captures its image.
    return `five native availability states; locale=${locale}; width=${innerWidth}`;
  } catch (error) {
    dispose();
    throw error;
  }
}
