import { useEffect, useState } from "react";
import { API_URL } from "./api";
import {
  parseTranslationCapabilities,
  type TranslationRegistry,
} from "./translationCapabilities";

/** Fetch once for the operator session; both setup surfaces share the same registry. */
export function useTranslationCapabilities(): TranslationRegistry {
  const [registry, setRegistry] = useState<TranslationRegistry>({
    status: "loading",
    providers: [],
  });
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/api/v1/translation/providers`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Provider discovery failed (HTTP ${response.status})`);
        return parseTranslationCapabilities(await response.json());
      })
      .then((providers) => {
        if (!controller.signal.aborted) setRegistry({ status: "ready", providers });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setRegistry({
            status: "error",
            providers: [],
            message: error instanceof Error ? error.message : "Provider discovery failed",
          });
        }
      });
    return () => controller.abort();
  }, []);
  return registry;
}
