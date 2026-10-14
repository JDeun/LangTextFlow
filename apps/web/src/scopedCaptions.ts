import type { SnapshotEvent, TranscriptEvent } from "./types";

export interface ScopedCaptions {
  path: string;
  records: Record<string, TranscriptEvent>;
}

export function createScopedCaptions(path: string): ScopedCaptions {
  return { path, records: {} };
}

export function applyScopedCaption(
  state: ScopedCaptions,
  path: string,
  payload: TranscriptEvent | SnapshotEvent,
): ScopedCaptions {
  // Messages from a disposed WebSocket/path are dropped, never transplanted
  // into a new audience session or a reconnecting operator session.
  if (state.path !== path) return state;
  if (payload.type === "snapshot") {
    return {
      path,
      records: Object.fromEntries(payload.segments.map((row) => [row.segment_id, row])),
    };
  }
  const previous = state.records[payload.segment_id];
  if (previous && previous.version >= payload.version) return state;
  return { path, records: { ...state.records, [payload.segment_id]: payload } };
}

export function visibleScopedCaptions(state: ScopedCaptions, path: string): TranscriptEvent[] {
  if (state.path !== path) return [];
  return Object.values(state.records).sort((a, b) => a.start_ms - b.start_ms);
}


/** Remove stale captions on a transient or terminal socket close.
 *
 * Reconnect repopulates from a fresh authoritative snapshot; an old-path
 * closure cannot clear the currently selected audience join-code route.
 */
export function clearDisconnectedCaptionScope(
  state: ScopedCaptions, disconnectedPath: string,
): ScopedCaptions {
  return state.path === disconnectedPath
    ? createScopedCaptions(disconnectedPath)
    : state;
}
