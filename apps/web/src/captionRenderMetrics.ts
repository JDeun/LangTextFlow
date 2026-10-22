import type { TranscriptEvent } from "./types";

/** Browser-local transport/paint observations; NOT audio-to-audience latency. */
export interface CaptionRenderObservation {
  segmentId: string;
  version: number;
  stage: TranscriptEvent["stage"];
  receiveToPaintMs: number;
  // True only for an approximate double-rAF post-paint observation.
  timingMethod: "double_raf_estimate";
  // Emitted-at is server wall time. Client/server clock skew makes this diagnostic only.
  serverEmitToPaintEstimateMs: number | null;
}

export function renderObservation(
  event: TranscriptEvent,
  receivedAtMonotonicMs: number,
  paintedAtMonotonicMs: number,
  paintedAtWallMs: number,
): CaptionRenderObservation {
  const emittedMs = Date.parse(event.emitted_at);
  return {
    segmentId: event.segment_id,
    version: event.version,
    stage: event.stage,
    receiveToPaintMs: Math.max(0, paintedAtMonotonicMs - receivedAtMonotonicMs),
    timingMethod: "double_raf_estimate",
    serverEmitToPaintEstimateMs: Number.isFinite(emittedMs)
      ? paintedAtWallMs - emittedMs : null,
  };
}

/** Only collect accepted visible versions; avoid replays and stale updates. */
export function shouldObservePaint(
  event: TranscriptEvent,
  visible: readonly TranscriptEvent[],
): boolean {
  return visible.some((item) =>
    item.segment_id === event.segment_id && item.version === event.version
  );
}

/** Bounded, in-memory statistics; no transcript text or persistent identifiers. */
export class CaptionRenderStats {
  private readonly samples = new Map<string, number[]>();
  private readonly maxSamplesPerStage: number;
  constructor(maxSamplesPerStage = 512) {
    this.maxSamplesPerStage = maxSamplesPerStage;
    if (!Number.isInteger(maxSamplesPerStage) || maxSamplesPerStage < 1) {
      throw new RangeError("maxSamplesPerStage must be a positive integer");
    }
  }

  add(observation: CaptionRenderObservation): void {
    if (!Number.isFinite(observation.receiveToPaintMs) ||
        observation.receiveToPaintMs < 0) return;
    const values = this.samples.get(observation.stage) ?? [];
    values.push(observation.receiveToPaintMs);
    if (values.length > this.maxSamplesPerStage) values.shift();
    this.samples.set(observation.stage, values);
  }

  summary(): Record<string, { count: number; p50Ms: number; p95Ms: number; p99Ms: number }> {
    const output: Record<string, {
      count: number; p50Ms: number; p95Ms: number; p99Ms: number
    }> = {};
    for (const [stage, samples] of this.samples) {
      const sorted = [...samples].sort((a, b) => a - b);
      const quantile = (p: number) => {
        const index = (sorted.length - 1) * p;
        const low = Math.floor(index);
        const high = Math.ceil(index);
        return sorted[low] + (sorted[high] - sorted[low]) * (index - low);
      };
      output[stage] = {
        count: sorted.length,
        p50Ms: quantile(0.5),
        p95Ms: quantile(0.95),
        p99Ms: quantile(0.99),
      };
    }
    return output;
  }

  reset(): void {
    this.samples.clear();
  }
}

/** One browser-tab collector. Values stay local and contain no transcript text. */
export const captionRenderStats = new CaptionRenderStats();

export const CAPTION_RENDER_METRIC_EVENT = "langtextflow:caption-render-metric";
export const CAPTION_RENDER_STATS_EVENT = "langtextflow:caption-render-stats";

export function installCaptionRenderCollector(target: Window): () => void {
  const collect = (raw: Event) => {
    if (!(raw instanceof CustomEvent)) return;
    const observation = raw.detail as CaptionRenderObservation | undefined;
    if (!observation || typeof observation.stage !== "string" ||
        typeof observation.receiveToPaintMs !== "number") return;
    captionRenderStats.add(observation);
    target.dispatchEvent(new CustomEvent(CAPTION_RENDER_STATS_EVENT, {
      detail: captionRenderStats.summary(),
    }));
  };
  target.addEventListener(CAPTION_RENDER_METRIC_EVENT, collect);
  return () => target.removeEventListener(CAPTION_RENDER_METRIC_EVENT, collect);
}
