import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL } from "./api";
import { COMPLETION_COPY } from "./completionCopy";
import {
  checkDesktopUpdate,
  desktopUpdateSupported,
  installDesktopUpdate,
} from "./desktopUpdate";
import { useI18n } from "./i18n";

interface RuntimeStatus {
  kind: "faster-whisper" | "ollama" | "vibevoice";
  available: boolean;
  managed: boolean;
  path: string | null;
  detail: string;
}

interface RuntimeJob {
  job_id: string;
  kind: RuntimeStatus["kind"];
  state: "queued" | "running" | "completed" | "cancelled" | "error";
  status: string;
  error: string | null;
}

interface CacheEntry {
  area: string;
  path: string;
  bytes: number;
  files: number;
}

interface CacheInventory {
  root: string;
  total_bytes: number;
  entries: CacheEntry[];
  filesystem_free_bytes: number;
  safety_reserve_bytes: number;
  writable_bytes: number;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}

export function RuntimeMaintenancePanel({ disabled }: { disabled: boolean }) {
  const { locale } = useI18n();
  const copy = COMPLETION_COPY[locale];
  const [runtimes, setRuntimes] = useState<RuntimeStatus[]>([]);
  const [jobs, setJobs] = useState<RuntimeJob[]>([]);
  const [cache, setCache] = useState<CacheInventory | null>(null);
  const [error, setError] = useState("");
  const [updateVersion, setUpdateVersion] = useState<string | null>(null);
  const [updateChecked, setUpdateChecked] = useState(false);
  const [updateBusy, setUpdateBusy] = useState(false);
  const updaterSupported = desktopUpdateSupported();

  const refresh = useCallback(async () => {
    try {
      const [runtimeResponse, jobsResponse, cacheResponse] = await Promise.all([
        fetch(`${API_URL}/api/v1/setup/runtimes`),
        fetch(`${API_URL}/api/v1/setup/runtime-jobs`),
        fetch(`${API_URL}/api/v1/setup/cache`),
      ]);
      if (!runtimeResponse.ok || !jobsResponse.ok || !cacheResponse.ok) {
        throw new Error("runtime maintenance request failed");
      }
      setRuntimes((await runtimeResponse.json()) as RuntimeStatus[]);
      setJobs((await jobsResponse.json()) as RuntimeJob[]);
      setCache((await cacheResponse.json()) as CacheInventory);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const activeKinds = useMemo(
    () => new Set(jobs.filter((job) => job.state === "queued" || job.state === "running").map((job) => job.kind)),
    [jobs],
  );

  useEffect(() => {
    if (activeKinds.size === 0) return;
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => window.clearInterval(timer);
  }, [activeKinds.size, refresh]);

  const provision = async (kind: RuntimeStatus["kind"]) => {
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/runtimes/${encodeURIComponent(kind)}`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const clearCache = async (area: string) => {
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/setup/cache/${encodeURIComponent(area)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await response.text());
      setCache((await response.json()) as CacheInventory);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const checkUpdate = async () => {
    setUpdateBusy(true);
    setError("");
    try {
      setUpdateVersion(await checkDesktopUpdate());
      setUpdateChecked(true);
    } catch (reason) {
      setUpdateVersion(null);
      setUpdateChecked(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUpdateBusy(false);
    }
  };

  const installUpdate = async () => {
    setUpdateBusy(true);
    setError("");
    try {
      await installDesktopUpdate();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setUpdateBusy(false);
    }
  };

  return (
    <section className="runtime-maintenance" data-testid="runtime-maintenance">
      <div className="section-heading-row">
        <div>
          <strong>{copy.runtimeTitle}</strong>
          <small>{copy.runtimeHelp}</small>
        </div>
        <button className="secondary-button" type="button" onClick={() => void refresh()}>{copy.refresh}</button>
      </div>
      <div className="runtime-grid">
        {runtimes.map((runtime) => {
          const active = activeKinds.has(runtime.kind);
          const latest = [...jobs].reverse().find((job) => job.kind === runtime.kind);
          return (
            <article className="runtime-card" key={runtime.kind}>
              <div><strong>{runtime.kind}</strong><span className={runtime.available ? "status-good" : "status-muted"}>{runtime.available ? copy.runtimeReady : copy.runtimeMissing}</span></div>
              <small>{latest?.state === "error" ? latest.error : runtime.detail}</small>
              {!runtime.available && (
                <button type="button" className="secondary-button" disabled={disabled || active} onClick={() => void provision(runtime.kind)}>
                  {active ? copy.installing : copy.install}
                </button>
              )}
            </article>
          );
        })}
      </div>
      {cache && (
        <div className="cache-panel">
          <div><strong>{copy.cacheTitle}</strong><small>{copy.cacheHelp}</small></div>
          <small>{formatBytes(cache.total_bytes)} · {cache.root}</small>
          <small>
            {copy.cacheHeadroom}: {formatBytes(cache.writable_bytes)} · {copy.cacheReserve}: {formatBytes(cache.safety_reserve_bytes)}
          </small>
          {cache.entries.map((entry) => (
            <div className="cache-row" key={entry.area}>
              <span>{entry.area} · {formatBytes(entry.bytes)} · {entry.files}</span>
              <button type="button" className="secondary-button" disabled={disabled || entry.bytes === 0} onClick={() => void clearCache(entry.area)}>{copy.clear}</button>
            </div>
          ))}
        </div>
      )}
      {updaterSupported && (
        <div className="cache-panel" data-testid="desktop-updater">
          <div><strong>{copy.updateTitle}</strong><small>{copy.updateHelp}</small></div>
          {updateChecked && (
            <small>
              {updateVersion ? `${copy.updateAvailable}: ${updateVersion}` : copy.updateCurrent}
            </small>
          )}
          <div className="cache-row">
            <span>{updateVersion || ""}</span>
            {updateVersion ? (
              <button
                type="button"
                className="secondary-button"
                disabled={disabled || updateBusy}
                onClick={() => void installUpdate()}
              >
                {updateBusy ? copy.updateInstalling : copy.updateInstall}
              </button>
            ) : (
              <button
                type="button"
                className="secondary-button"
                disabled={disabled || updateBusy}
                onClick={() => void checkUpdate()}
              >
                {updateBusy ? copy.updateChecking : copy.updateCheck}
              </button>
            )}
          </div>
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
    </section>
  );
}
