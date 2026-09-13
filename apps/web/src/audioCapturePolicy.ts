export interface AudioSocketConfig {
  type: "audio_config";
  sample_rate: number;
  channels: number;
  sample_format: "f32le";
}

export const MAX_AUDIO_SOCKET_BUFFERED_BYTES = 1024 * 1024;
export const AUDIO_PROTOCOL_ERROR_CODE = 4003;
export const AUDIO_BACKPRESSURE_CLOSE_CODE = 4013;

export function canQueueAudioFrame(
  bufferedAmount: number,
  frameBytes: number,
  maxBufferedBytes = MAX_AUDIO_SOCKET_BUFFERED_BYTES,
): boolean {
  return (
    Number.isFinite(bufferedAmount) &&
    Number.isFinite(frameBytes) &&
    bufferedAmount >= 0 &&
    frameBytes >= 0 &&
    bufferedAmount + frameBytes <= maxBufferedBytes
  );
}

export function parseAudioSocketConfig(value: string): AudioSocketConfig {
  let payload: unknown;
  try {
    payload = JSON.parse(value);
  } catch {
    throw new Error("오디오 서버가 잘못된 설정 응답을 반환했습니다.");
  }
  if (!payload || typeof payload !== "object") {
    throw new Error("오디오 서버가 잘못된 설정 응답을 반환했습니다.");
  }
  const candidate = payload as Partial<AudioSocketConfig>;
  if (
    candidate.type !== "audio_config" ||
    !Number.isInteger(candidate.sample_rate) ||
    (candidate.sample_rate ?? 0) < 8000 ||
    (candidate.sample_rate ?? 0) > 192000 ||
    candidate.channels !== 1 ||
    candidate.sample_format !== "f32le"
  ) {
    throw new Error("오디오 서버 설정이 지원 범위를 벗어났습니다.");
  }
  return candidate as AudioSocketConfig;
}
