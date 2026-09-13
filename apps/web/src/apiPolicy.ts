export function resolveApiUrl(
  configuredApiUrl: string | undefined,
  location: Pick<Location, "protocol" | "hostname">,
) {
  if (configuredApiUrl) return configuredApiUrl.replace(/\/$/, "");

  const isTauri =
    location.protocol === "tauri:" || location.hostname === "tauri.localhost";
  if (isTauri) return "http://127.0.0.1:8000";

  const protocol = location.protocol === "https:" ? "https:" : "http:";
  return `${protocol}//${location.hostname}:8000`;
}
