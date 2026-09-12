const configuredApiUrl = import.meta.env.VITE_API_URL as string | undefined;

export function getApiUrl() {
  if (configuredApiUrl) return configuredApiUrl.replace(/\/$/, "");
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  return `${protocol}//${window.location.hostname}:8000`;
}

export function getWebSocketUrl(path: string) {
  return getApiUrl().replace(/^http/, "ws") + path;
}

export const API_URL = getApiUrl();
