import { useEffect, useMemo, useState } from "react";
import "./telemetry.css";
import { useI18n } from "./i18n";
import type { RealtimeMetrics } from "./types";
import { UTILITY_COPY } from "./utilityCopy";

interface TelemetryPanelProps {
  apiUrl: string;
  running: boolean;
}

function milliseconds(value: number | null) {
  if (value === null) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${Math.round(value)} ms`;
}

function queueValue(depth: number, capacity: number) {
  return capacity > 0 ? `${depth} / ${capacity}` : `${depth}`;
}

function queueLevel(depth: number, capacity: number) {
  if (capacity <= 0) return "normal";
  const ratio = depth / capacity;
  if (ratio >= 0.8) return "danger";
  if (ratio >= 0.5) return "warning";
  return "normal";
}

function latencyLevel(value: number | null, warningMs: number, dangerMs: number) {
  if (value === null) return "normal";
  if (value >= dangerMs) return "danger";
  if (value >= warningMs) return "warning";
  return "normal";
}

function diagnosticsFilename(disposition: string | null) {
  const match = disposition?.match(/filename="([^"]+)"/i);
  return match?.[1] || "langtextflow-diagnostics.zip";
}

export function TelemetryPanel({ apiUrl, running }: TelemetryPanelProps) {
  const { locale } = useI18n();
  const copy = UTILITY_COPY[locale].telemetry;
  const [metrics, setMetrics] = useState<RealtimeMetrics | null>(null);
  const [error, setError] = useState("");
  const [diagnosticsBusy, setDiagnosticsBusy] = useState(false);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;

    const load = async () => {
      try {
        const response = await fetch(`${apiUrl}/api/v1/metrics`);
        if (!response.ok) throw new Error(`metrics HTTP ${response.status}`);
        const payload = (await response.json()) as RealtimeMetrics;
        if (!disposed) {
          setMetrics(payload);
          setError("");
        }
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : copy.unavailable);
      }
    };

    void load();
    if (running) timer = window.setInterval(load, 750);
    return () => {
      disposed = true;
      if (timer) window.clearInterval(timer);
    };
  }, [apiUrl, copy.unavailable, running]);

  async function downloadDiagnostics() {
    setDiagnosticsBusy(true);
    setError("");
    try {
      const response = await fetch(`${apiUrl}/api/v1/diagnostics`);
      if (!response.ok) throw new Error(`diagnostics HTTP ${response.status}`);
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = diagnosticsFilename(response.headers.get("content-disposition"));
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.diagnosticsFailed);
    } finally {
      setDiagnosticsBusy(false);
    }
  }

  const audioSeconds = useMemo(
    () => (metrics ? metrics.audio_duration_ms / 1000 : 0),
    [metrics],
  );
  const providerFailed = Boolean(metrics?.asr_failure);
  const providerHealthy = Boolean(running && metrics?.asr_running && !providerFailed);
  const providerRecovered = Boolean(providerHealthy && (metrics?.asr_failover_count ?? 0) > 0);
  const statusLabel = providerFailed
    ? "FAILED"
    : providerHealthy
      ? "LIVE"
      : running
        ? "STARTING"
        : "IDLE";
  const statusClass = providerFailed
    ? "telemetry-failed"
    : providerHealthy
      ? "telemetry-live"
      : "telemetry-idle";

  return (
    <section className="telemetry-card">
      <div className="telemetry-heading">
        <span>{copy.title}</span>
        <div className="telemetry-actions">
          <button
            type="button"
            className="secondary-button telemetry-diagnostics"
            onClick={downloadDiagnostics}
            disabled={diagnosticsBusy}
          >
            {diagnosticsBusy ? copy.collecting : copy.diagnostics}
          </button>
          <span className={statusClass}>{statusLabel}</span>
        </div>
      </div>

      {error && <div className="telemetry-empty">{error}</div>}
      {metrics && (
        <>
          <div
            className={`provider-health ${
              providerFailed ? "failed" : providerRecovered ? "recovered" : providerHealthy ? "healthy" : "idle"
            }`}
          >
            <div>
              <span>{copy.provider}</span>
              <strong>{metrics.asr_provider || "—"}</strong>
            </div>
            <small>
              {metrics.asr_failure ||
                (providerRecovered
                  ? `${copy.recovered} · ${metrics.asr_failover_count} ${copy.failover} · ${metrics.asr_last_failover_reason || copy.handoff}`
                  : metrics.asr_running
                    ? copy.healthy
                    : running
                      ? copy.notRunning
                      : copy.idle)}
            </small>
          </div>

          <div className="telemetry-input">
            <span className={`voice-indicator ${metrics.voice_active ? "active" : ""}`} />
            <div>
              <strong>{metrics.voice_active ? copy.voice : copy.noVoice}</strong>
              <small>
                {metrics.audio_rms_dbfs === null ? "—" : `${metrics.audio_rms_dbfs.toFixed(1)} dBFS`}
                {metrics.audio_frames_received > 0 ? ` · ${audioSeconds.toFixed(1)} s ${copy.audio}` : ""}
              </small>
            </div>
          </div>

          <div className="telemetry-grid">
            <div className={`telemetry-metric ${queueLevel(metrics.asr_queue_depth, metrics.asr_queue_capacity)}`}>
              <span>{copy.asrQueue}</span>
              <strong>{queueValue(metrics.asr_queue_depth, metrics.asr_queue_capacity)}</strong>
              <small>{copy.peak} {metrics.asr_queue_high_watermark}</small>
            </div>
            <div className={`telemetry-metric ${metrics.asr_failover_count > 0 ? "warning" : "normal"}`}>
              <span>{copy.failovers}</span>
              <strong>{metrics.asr_failover_count}</strong>
              <small>{metrics.asr_failover_count > 0 ? copy.recoveredHandoff : copy.none}</small>
            </div>
            <div className={`telemetry-metric ${queueLevel(metrics.postprocess_queue_depth, metrics.postprocess_queue_capacity)}`}>
              <span>{copy.postprocess}</span>
              <strong>{queueValue(metrics.postprocess_queue_depth, metrics.postprocess_queue_capacity)}</strong>
              <small>{copy.correctionTranslation}</small>
            </div>
            <div className={`telemetry-metric ${queueLevel(metrics.persistence_queue_depth, metrics.persistence_queue_capacity)}`}>
              <span>{copy.storage}</span>
              <strong>{queueValue(metrics.persistence_queue_depth, metrics.persistence_queue_capacity)}</strong>
              <small>{copy.writer}</small>
            </div>
            <div className={`telemetry-metric ${latencyLevel(metrics.last_audio_enqueue_wait_ms, 50, 200)}`}>
              <span>{copy.enqueue}</span>
              <strong>{milliseconds(metrics.last_audio_enqueue_wait_ms)}</strong>
              <small>{metrics.audio_backpressure_events} {copy.backpressure}</small>
            </div>
            <div className={`telemetry-metric ${latencyLevel(metrics.last_asr_lag_ms, 1500, 3000)}`}>
              <span>{copy.lag}</span>
              <strong>{milliseconds(metrics.last_asr_lag_ms)}</strong>
              <small>{copy.lagHelp}</small>
            </div>
            <div className={`telemetry-metric ${latencyLevel(metrics.last_correction_latency_ms, 250, 800)}`}>
              <span>{copy.correction}</span>
              <strong>{milliseconds(metrics.last_correction_latency_ms)}</strong>
              <small>{copy.correctionHelp}</small>
            </div>
            <div className={`telemetry-metric ${latencyLevel(metrics.last_translation_latency_ms, 1000, 2500)}`}>
              <span>{copy.translation}</span>
              <strong>{milliseconds(metrics.last_translation_latency_ms)}</strong>
              <small>{copy.translationHelp}</small>
            </div>
            <div className={`telemetry-metric ${latencyLevel(metrics.last_commit_latency_ms, 1500, 3000)}`}>
              <span>{copy.commit}</span>
              <strong>{milliseconds(metrics.last_commit_latency_ms)}</strong>
              <small>{copy.commitHelp}</small>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
