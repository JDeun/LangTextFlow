import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { useI18n } from "./i18n";
import type { VibeVoiceLifecycleState } from "./types";
import { UTILITY_COPY } from "./utilityCopy";
import "./vibevoiceLifecycle.css";

interface VibeVoiceLifecycleControlProps {
  enabled: boolean;
  onReady?: () => void;
}

export function VibeVoiceLifecycleControl({
  enabled,
  onReady,
}: VibeVoiceLifecycleControlProps) {
  const { locale } = useI18n();
  const copy = UTILITY_COPY[locale].vibe;
  const [state, setState] = useState<VibeVoiceLifecycleState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readyNotified = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/vibevoice`);
      if (!response.ok) throw new Error(`${copy.statusHttp} ${response.status}`);
      const next = (await response.json()) as VibeVoiceLifecycleState;
      setState(next);
      if (next.healthy && !readyNotified.current) {
        readyNotified.current = true;
        onReady?.();
      }
      if (!next.healthy) readyNotified.current = false;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.statusFailed);
    }
  }, [copy.statusFailed, copy.statusHttp, enabled, onReady]);

  useEffect(() => {
    if (!enabled) return;
    void load();
  }, [enabled, load]);

  useEffect(() => {
    if (!enabled || state?.mode !== "starting") return;
    const timer = window.setInterval(() => void load(), 700);
    return () => window.clearInterval(timer);
  }, [enabled, load, state?.mode]);

  async function mutate(action: "start" | "stop") {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/vibevoice/${action}`, {
        method: "POST",
      });
      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `VibeVoice ${action} HTTP ${response.status}`);
      }
      const next = (await response.json()) as VibeVoiceLifecycleState;
      setState(next);
      if (next.healthy) {
        readyNotified.current = true;
        onReady?.();
      } else {
        readyNotified.current = false;
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `VibeVoice ${action} ${copy.actionFailed}`);
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;

  const stateTitle = state?.mode === "external"
    ? copy.external
    : state?.mode === "managed"
      ? copy.managed
      : state?.mode === "starting"
        ? copy.starting
        : state?.mode === "stopped"
          ? copy.stopped
          : state?.mode === "unconfigured"
            ? copy.unconfigured
            : state?.mode === "error"
              ? copy.error
              : copy.checking;

  return (
    <div className={`vibevoice-lifecycle ${state?.mode ?? "loading"}`}>
      <div className="vibevoice-lifecycle-heading">
        <div>
          <small>VibeVoice lifecycle</small>
          <strong>{stateTitle}</strong>
        </div>
        <span>{state?.mode ?? "loading"}</span>
      </div>

      {state && <p>{state.status}</p>}
      {state?.pid !== null && state?.pid !== undefined && (
        <div className="vibevoice-lifecycle-meta">
          <span>PID {state.pid}</span>
          <span>{state.url}</span>
        </div>
      )}
      {state?.error && <div className="vibevoice-lifecycle-error">{state.error}</div>}
      {error && <div className="vibevoice-lifecycle-error">{error}</div>}

      {state?.log_tail.length ? (
        <details className="vibevoice-lifecycle-logs">
          <summary>{copy.logs}</summary>
          <pre>{state.log_tail.slice(-12).join("\n")}</pre>
        </details>
      ) : null}

      <div className="vibevoice-lifecycle-actions">
        {(state?.mode === "stopped" || state?.mode === "error") && state.configured && (
          <button onClick={() => void mutate("start")} disabled={busy}>
            {busy ? copy.startRequest : copy.start}
          </button>
        )}
        {(state?.mode === "starting" || state?.mode === "managed") && (
          <button onClick={() => void mutate("stop")} disabled={busy}>
            {busy ? copy.stopRequest : state.mode === "starting" ? copy.cancelStart : copy.stop}
          </button>
        )}
        <button onClick={() => void load()} disabled={busy}>
          {copy.refresh}
        </button>
      </div>
    </div>
  );
}
