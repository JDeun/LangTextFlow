export const BASE_RETRY_MS = 750;
export const MAX_RETRY_MS = 15_000;
export const RETRY_JITTER_RATIO = 0.2;

const TERMINAL_CLOSE_CODES = new Set([4403, 4404]);

export function isTerminalCloseCode(code: number) {
  return TERMINAL_CLOSE_CODES.has(code);
}

export function retryAfterMs(reason: string) {
  const match = reason.match(/retry after\s+(\d+)s/i);
  if (!match) return null;
  return Math.max(1, Number(match[1])) * 1000;
}

export function retryDelayMs(attempt: number, randomValue = Math.random()) {
  const exponential = Math.min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** Math.max(0, attempt));
  const boundedRandom = Math.min(1, Math.max(0, randomValue));
  const jitter = exponential * RETRY_JITTER_RATIO * (boundedRandom * 2 - 1);
  return Math.max(BASE_RETRY_MS, Math.round(exponential + jitter));
}

export function terminalMessage(code: number, reason: string) {
  if (code === 4403) return reason || "이 WebSocket 연결은 허용되지 않습니다.";
  if (code === 4404) return reason || "세션을 찾을 수 없거나 종료되었습니다.";
  return "";
}
