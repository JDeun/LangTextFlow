import { useEffect } from "react";
import { createAudienceLatencyReport } from "./audienceLatencyReport";
import type { CaptionRenderObservation } from "./captionRenderMetrics";

export function useAudienceLatencyReporting(apiUrl: string, joinCode: string, optedIn: boolean) {
  useEffect(() => {
    if (!optedIn) return;
    const controller = new AbortController();
    let lastSent = -Infinity;
    let pending = false;
    const report = (event: Event) => {
      const detail = (event as CustomEvent<CaptionRenderObservation>).detail;
      if (!detail || pending || performance.now() - lastSent < 6000) return;
      const sample = createAudienceLatencyReport(detail.stage, detail.receiveToPaintMs, true);
      if (!sample) return;
      lastSent = performance.now();
      pending = true;
      void fetch(`${apiUrl}/api/v1/audience/${encodeURIComponent(joinCode)}/metrics/render`, {
        method: "POST", credentials: "omit", cache: "no-store", signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Audience-Metrics-Consent": "true" },
        body: JSON.stringify({
          stage: sample.stage, receive_to_display_ms: sample.receiveToDisplayMs,
          timing_method: sample.timingMethod,
        }),
      }).catch(() => {}).finally(() => { pending = false; });
    };
    window.addEventListener("langtextflow:caption-render-metric", report);
    return () => {
      window.removeEventListener("langtextflow:caption-render-metric", report);
      controller.abort();
    };
  }, [apiUrl, joinCode, optedIn]);
}
