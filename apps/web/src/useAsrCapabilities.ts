import { useEffect, useState } from "react";
import { API_URL } from "./api";
import { parseAsrCapabilities, type AsrRegistry } from "./asrCapabilities";

export function useAsrCapabilities(): AsrRegistry {
  const [registry, setRegistry] = useState<AsrRegistry>({ status: "loading", providers: [] });
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/api/v1/asr/providers`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`ASR discovery HTTP ${response.status}`);
        return parseAsrCapabilities(await response.json());
      })
      .then((providers) => {
        if (!controller.signal.aborted) setRegistry({ status: "ready", providers });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setRegistry({
          status: "error", providers: [],
          message: error instanceof Error ? error.message : "ASR discovery failed",
        });
      });
    return () => controller.abort();
  }, []);
  return registry;
}
