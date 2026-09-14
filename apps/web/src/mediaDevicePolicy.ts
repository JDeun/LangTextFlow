export type MediaCaptureFailure =
  | "permission-denied"
  | "device-missing"
  | "device-busy"
  | "constraint-failed"
  | "capture-aborted"
  | "unsupported"
  | "unknown";

export function classifyMediaCaptureError(error: unknown): MediaCaptureFailure {
  if (!(error instanceof DOMException)) return "unknown";
  switch (error.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "permission-denied";
    case "NotFoundError":
      return "device-missing";
    case "NotReadableError":
    case "TrackStartError":
      return "device-busy";
    case "OverconstrainedError":
    case "ConstraintNotSatisfiedError":
      return "constraint-failed";
    case "AbortError":
      return "capture-aborted";
    case "NotSupportedError":
      return "unsupported";
    default:
      return "unknown";
  }
}

export function mediaCaptureErrorMessage(error: unknown): string {
  switch (classifyMediaCaptureError(error)) {
    case "permission-denied":
      return "마이크 권한이 거부되었습니다. 운영체제/브라우저 설정에서 LangTextFlow의 마이크 권한을 허용한 뒤 다시 시도하세요.";
    case "device-missing":
      return "선택한 마이크를 찾을 수 없습니다. 장치를 다시 연결하거나 다른 입력 장치를 선택하세요.";
    case "device-busy":
      return "마이크를 열 수 없습니다. 다른 앱이 장치를 독점하고 있는지 확인한 뒤 다시 시도하세요.";
    case "constraint-failed":
      return "선택한 마이크가 요청한 오디오 형식을 지원하지 않습니다. 다른 입력 장치를 선택하세요.";
    case "capture-aborted":
      return "마이크 초기화가 중단되었습니다. 장치 연결 상태를 확인하고 다시 시도하세요.";
    case "unsupported":
      return "이 환경에서는 필요한 마이크 캡처 기능을 지원하지 않습니다.";
    default:
      return error instanceof Error
        ? `마이크를 시작하지 못했습니다: ${error.message}`
        : "마이크를 시작하지 못했습니다.";
  }
}
