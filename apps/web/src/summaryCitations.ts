import type { TranscriptEvent } from "./types";

/** Link only exact, committed source markers from this session's loaded evidence. */
export function summaryCitations(text: string, segments: readonly Pick<TranscriptEvent, "segment_id" | "start_ms" | "committed">[]) {
  const source = new Map(segments.filter(segment => segment.committed)
    .map(segment => [segment.segment_id, segment]));
  const references = new Map<string, { segmentId: string; seconds: number }>();
  for (const match of text.matchAll(/\[([A-Za-z0-9][A-Za-z0-9_.:-]{0,127}) at (\d{1,7})s\]/g)) {
    const segment = source.get(match[1]);
    const seconds = Number(match[2]);
    if (segment && Math.floor(segment.start_ms / 1000) === seconds) {
      references.set(segment.segment_id, { segmentId: segment.segment_id, seconds });
    }
  }
  return [...references.values()];
}
