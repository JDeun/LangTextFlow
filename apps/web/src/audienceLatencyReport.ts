/** Privacy-first client-side policy for future audience latency reporting.
 * The transport is deliberately NOT enabled by this module.
 */
export interface AudienceLatencyReport {
  stage: "partial" | "stable" | "translated" | "committed";
  receiveToDisplayMs: number;
  timingMethod: "double_raf_estimate";
}

export const AUDIENCE_LATENCY_REPORTING_DEFAULT_ENABLED = false;
export const MAX_AUDIENCE_LATENCY_MS = 60_000;

export function createAudienceLatencyReport(
  stage: string,
  latencyMs: number,
  optedIn: boolean,
): AudienceLatencyReport | null {
  if (!optedIn || !Number.isFinite(latencyMs) || latencyMs < 0 ||
      latencyMs > MAX_AUDIENCE_LATENCY_MS) return null;
  if (stage !== "partial" && stage !== "stable" &&
      stage !== "translated" && stage !== "committed") return null;
  return {
    stage,
    receiveToDisplayMs: Math.round(latencyMs),
    timingMethod: "double_raf_estimate",
  };
}
