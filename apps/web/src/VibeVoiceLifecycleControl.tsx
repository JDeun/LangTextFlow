import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import type { VibeVoiceLifecycleState } from "./types";
import "./vibevoiceLifecycle.css";

interface VibeVoiceLifecycleControlProps {
  enabled: boolean;
  onReady?: () => void;
}

export function VibeVoiceLifecycleControl({
  enabled,
  onReady,
}: VibeVoiceLifecycleControlProps) {
  const [state, setState] = useState<VibeVoiceLifecycleState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const readyNotified = useRef(false);

  const load = useCallback(async () => {
    if (!enabled) return;
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/vibevoice`);
      if (!response.ok) throw new Error(`VibeVoice 상태 HTTP ${response.status}`);
      const next = (await response.json()) as VibeVoiceLifecycleState;
      setState(next);
      if (next.healthy && !readyNotified.current) {
        readyNotified.current = true;
        onReady?.();
      }
      if (!next.healthy) readyNotified.current = false;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "VibeVoice 상태를 확인하지 못했습니다.");
    }
  }, [enabled, onReady]);

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
      setError(reason instanceof Error ? reason.message : `VibeVoice ${action}에 실패했습니다.`);
    } finally {
      setBusy(false);
    }
  }

  if (!enabled) return null;

  return (
    <div className={`vibevoice-lifecycle ${state?.mode ?? "loading"}`}>
      <div className="vibevoice-lifecycle-heading">
        <div>
          <small>VibeVoice lifecycle</small>
          <strong>
            {state?.mode === "external"
              ? "외부 sidecar 연결됨"
              : state?.mode === "managed"
                ? "관리형 sidecar READY"
                : state?.mode === "starting"
                  ? "sidecar 시작 중"
                  : state?.mode === "stopped"
                    ? "sidecar 시작 가능"
                    : state?.mode === "unconfigured"
                      ? "관리형 실행 미구성"
                      : state?.mode === "error"
                        ? "sidecar 오류"
                        : "상태 확인 중"}
          </strong>
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
          <summary>최근 실행 로그</summary>
          <pre>{state.log_tail.slice(-12).join("\n")}</pre>
        </details>
      ) : null}

      <div className="vibevoice-lifecycle-actions">
        {(state?.mode === "stopped" || state?.mode === "error") && state.configured && (
          <button onClick={() => void mutate("start")} disabled={busy}>
            {busy ? "시작 요청 중…" : "VibeVoice 시작"}
          </button>
        )}
        {(state?.mode === "starting" || state?.mode === "managed") && (
          <button onClick={() => void mutate("stop")} disabled={busy}>
            {busy ? "중지 요청 중…" : state.mode === "starting" ? "시작 취소" : "VibeVoice 종료"}
          </button>
        )}
        <button onClick={() => void load()} disabled={busy}>
          상태 새로고침
        </button>
      </div>
    </div>
  );
}
