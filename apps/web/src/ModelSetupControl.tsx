import { useEffect, useMemo, useState } from "react";
import { API_URL } from "./api";
import type { ModelSetupJob } from "./types";

interface ModelSetupControlProps {
  model: string;
  enabled: boolean;
  onCompleted?: () => void;
}

const TERMINAL = new Set(["completed", "cancelled", "error"]);

function formatBytes(value: number | null) {
  if (value === null || value < 0) return null;
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(0)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(0)} KB`;
  return `${value} B`;
}

export function ModelSetupControl({ model, enabled, onCompleted }: ModelSetupControlProps) {
  const [job, setJob] = useState<ModelSetupJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const active = job !== null && !TERMINAL.has(job.state);
  const progressLabel = useMemo(() => {
    if (!job) return "";
    if (job.progress_percent !== null) return `${job.progress_percent.toFixed(1)}%`;
    const completed = formatBytes(job.completed_bytes);
    const total = formatBytes(job.total_bytes);
    if (completed && total) return `${completed} / ${total}`;
    return job.status;
  }, [job]);

  useEffect(() => {
    if (!active || !job) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_URL}/api/v1/setup/jobs/${encodeURIComponent(job.job_id)}`);
        if (!response.ok) throw new Error(`모델 준비 상태 HTTP ${response.status}`);
        const next = (await response.json()) as ModelSetupJob;
        if (cancelled) return;
        setJob(next);
        if (next.state === "completed") onCompleted?.();
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "모델 준비 상태를 확인하지 못했습니다.");
        }
      }
    }, 650);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, job, onCompleted]);

  useEffect(() => {
    if (job && job.model !== model.trim()) {
      setJob(null);
      setError("");
    }
  }, [job, model]);

  async function start() {
    if (!enabled || !model.trim()) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/ollama/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: model.trim() }),
      });
      if (!response.ok) throw new Error(await response.text());
      setJob((await response.json()) as ModelSetupJob);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "모델 다운로드를 시작하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!job || !active) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `${API_URL}/api/v1/setup/jobs/${encodeURIComponent(job.job_id)}/cancel`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await response.text());
      setJob((await response.json()) as ModelSetupJob);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "다운로드 요청을 취소하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (!enabled && !job) return null;

  return (
    <div className="model-setup-control">
      {!job && (
        <button onClick={start} disabled={busy || !enabled || !model.trim()}>
          {busy ? "준비 중…" : `${model || "번역 모델"} 다운로드`}
        </button>
      )}

      {job && (
        <div className={`model-setup-job ${job.state}`}>
          <div className="model-setup-heading">
            <div>
              <small>{job.provider} model setup</small>
              <strong>{job.model}</strong>
            </div>
            <span>{job.state}</span>
          </div>
          <div className={`model-setup-progress ${job.progress_percent === null ? "indeterminate" : ""}`}>
            <i style={{ width: `${job.progress_percent ?? 35}%` }} />
          </div>
          <div className="model-setup-meta">
            <span>{job.status}</span>
            <b>{progressLabel}</b>
          </div>
          {job.error && <div className="model-setup-error">{job.error}</div>}
          {active && (
            <button onClick={cancel} disabled={busy}>
              {busy ? "취소 중…" : "다운로드 요청 취소"}
            </button>
          )}
          {TERMINAL.has(job.state) && job.state !== "completed" && (
            <button onClick={() => setJob(null)}>다시 시도</button>
          )}
        </div>
      )}

      {error && <div className="model-setup-error">{error}</div>}
    </div>
  );
}
