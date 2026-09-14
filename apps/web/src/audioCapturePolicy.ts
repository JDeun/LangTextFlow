export interface AudioSocketConfig {
  type: "audio_config";
  sample_rate: number;
  channels: number;
  sample_format: "f32le";
}

export interface RecoverableAudioDevice {
  deviceId: string;
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

export function microphoneErrorMessage(error: unknown): string {
  const name =
    error && typeof error === "object" && "name" in error
      ? String((error as { name?: unknown }).name || "")
      : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "마이크 권한이 차단되었습니다. 운영체제/브라우저 설정에서 LangTextFlow의 마이크 접근을 허용한 뒤 다시 시도하세요.";
    case "NotFoundError":
      return "사용 가능한 마이크를 찾지 못했습니다. 마이크 연결 상태를 확인한 뒤 입력 장치를 새로고침하세요.";
    case "OverconstrainedError":
      return "선택한 마이크를 더 이상 사용할 수 없습니다. 입력 장치를 새로고침하고 다른 마이크를 선택하세요.";
    case "NotReadableError":
    case "AbortError":
      return "마이크를 열 수 없습니다. 다른 앱이 마이크를 독점 사용 중인지 확인한 뒤 다시 시도하세요.";
    default:
      return error instanceof Error && error.message
        ? error.message
        : "마이크 입력을 시작하지 못했습니다.";
  }
}

export function chooseRecoveryDevice(
  preferredDeviceId: string | undefined,
  devices: RecoverableAudioDevice[],
): string | undefined {
  if (preferredDeviceId && devices.some((device) => device.deviceId === preferredDeviceId)) {
    return preferredDeviceId;
  }
  const explicitDefault = devices.find((device) => device.deviceId === "default")?.deviceId;
  return explicitDefault || devices[0]?.deviceId;
}
