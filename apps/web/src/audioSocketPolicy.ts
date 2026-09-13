export interface AudioSocketConfig {
  type: "audio_config";
  sample_rate: number;
  channels: 1;
  sample_format: "f32le";
}

export const MAX_AUDIO_SOCKET_BUFFER_BYTES = 512 * 1024;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseAudioSocketConfig(raw: unknown): AudioSocketConfig | null {
  if (typeof raw !== "string") return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw) as unknown;
  } catch {
    return null;
  }
  if (!isRecord(parsed)) return null;
  if (parsed.type !== "audio_config") return null;
  if (
    typeof parsed.sample_rate !== "number" ||
    !Number.isInteger(parsed.sample_rate) ||
    parsed.sample_rate < 8_000 ||
    parsed.sample_rate > 192_000
  ) {
    return null;
  }
  if (parsed.channels !== 1 || parsed.sample_format !== "f32le") return null;

  return parsed as unknown as AudioSocketConfig;
}

export function shouldSendAudioFrame(
  bufferedAmount: number,
  limit = MAX_AUDIO_SOCKET_BUFFER_BYTES,
): boolean {
  return (
    Number.isFinite(bufferedAmount) &&
    bufferedAmount >= 0 &&
    bufferedAmount <= Math.max(0, limit)
  );
}
