export const SUPPORTED_LOCALES = ["en", "ko", "ja"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

export const LOCALE_STORAGE_KEY = "langtextflow:locale:v1";

export function normalizeLocale(value: string | null | undefined): Locale | null {
  if (!value) return null;
  const normalized = value.trim().toLowerCase();
  if (normalized.startsWith("ko")) return "ko";
  if (normalized.startsWith("ja")) return "ja";
  if (normalized.startsWith("en")) return "en";
  return null;
}

export function resolveInitialLocale(stored: string | null, browserLanguage: string): Locale {
  const explicit = stored && SUPPORTED_LOCALES.includes(stored as Locale)
    ? (stored as Locale)
    : null;
  return explicit ?? normalizeLocale(browserLanguage) ?? "en";
}
