import { useEffect, useRef, useState } from "react";
import { glossarySnapshotScope, parseGlossarySnapshot, type GlossaryResolveRequest } from "./glossarySnapshot";
import type { ResolvedGlossarySnapshot } from "./types";

type Projection = { scope: string; state: "loading" | "ready" | "error"; snapshot?: ResolvedGlossarySnapshot };

export function useGlossarySnapshot(apiUrl: string, request: GlossaryResolveRequest, enabled: boolean) {
  const scope = JSON.stringify([apiUrl, glossarySnapshotScope(request)]);
  const active = useRef({ scope, enabled });
  active.current = { scope, enabled };
  const epoch = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [projection, setProjection] = useState<Projection | null>(null);
  const visible = enabled && projection?.scope === scope ? projection : null;

  useEffect(() => {
    epoch.current++;
    controller.current?.abort();
    setProjection(null);
    return () => { epoch.current++; controller.current?.abort(); };
  }, [scope, enabled]);

  function invalidate() {
    epoch.current++;
    controller.current?.abort();
    setProjection(null);
  }

  async function preview() {
    if (!enabled) return;
    const attempt = ++epoch.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const current = () => !abort.signal.aborted && attempt === epoch.current
      && active.current.scope === scope && active.current.enabled;
    setProjection({ scope, state: "loading" });
    try {
      const response = await fetch(`${apiUrl}/api/v1/glossary/resolve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        // Resolve terminology only; do not resend uploaded reference files,
        // microphone/metrics consent, or provider/model settings for preview.
        body: JSON.stringify({ source_language: request.source_language,
          target_languages: request.target_languages, project_id: request.project_id,
          context: { preset: request.context.preset, glossary: request.context.glossary },
        }), signal: abort.signal,
      });
      if (!current()) return;
      if (!response.ok) throw new Error("Glossary resolution failed");
      const value: unknown = await response.json();
      if (!current()) return;
      setProjection({ scope, state: "ready", snapshot: parseGlossarySnapshot(value, request) });
    } catch {
      if (current()) setProjection({ scope, state: "error" });
    }
  }

  return { snapshot: visible?.snapshot ?? null, loading: visible?.state === "loading",
    failed: visible?.state === "error", preview, invalidate };
}
