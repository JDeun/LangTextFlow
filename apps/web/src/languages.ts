export const LANGUAGE_OPTIONS = [
  ["ko", "한국어"],
  ["en", "English"],
  ["ja", "日本語"],
  ["zh", "中文"],
  ["es", "Español"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["it", "Italiano"],
  ["pt", "Português"],
  ["ru", "Русский"],
] as const;

export function languageLabel(code: string) {
  return LANGUAGE_OPTIONS.find(([value]) => value === code)?.[1] ?? code.toUpperCase();
}
