interface TimedRecording {
  recording_id: string;
  duration_seconds: number;
  start_offset_ms?: number | null;
}

/** Live source times use the session PCM clock; offline drafts use their own file. */
export function recordingAtSessionTime(recordings: TimedRecording[], startMs: number) {
  if (!Number.isFinite(startMs) || startMs < 0) return null;
  const matches = recordings.filter(item => typeof item.start_offset_ms === "number"
    && Number.isFinite(item.start_offset_ms) && item.start_offset_ms >= 0
    && Number.isFinite(item.duration_seconds) && item.duration_seconds > 0
    && startMs >= item.start_offset_ms
    && startMs < item.start_offset_ms + item.duration_seconds * 1000);
  matches.sort((a, b) => b.start_offset_ms! - a.start_offset_ms! || a.recording_id.localeCompare(b.recording_id));
  const selected = matches[0];
  return selected ? { id: selected.recording_id, startMs: startMs - selected.start_offset_ms! } : null;
}
