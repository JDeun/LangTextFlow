import { resolveApiUrl } from "./apiPolicy";

const configuredApiUrl = import.meta.env.VITE_API_URL as string | undefined;

export function getApiUrl() {
  return resolveApiUrl(configuredApiUrl, window.location);
}

export function getWebSocketUrl(path: string) {
  return getApiUrl().replace(/^http/, "ws") + path;
}

export const API_URL = getApiUrl();
