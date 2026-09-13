import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL } from "./api";
import { useI18n } from "./i18n";
import { ModelSetupControl } from "./ModelSetupControl";
import { PREFLIGHT_COPY } from "./preflightCopy";
import type {
  PreflightCheck,
  RecommendedConfiguration,
  SystemPreflight,
} from "./types";
import { VibeVoiceLifecycleControl } from "./VibeVoiceLifecycleControl";
import "./preflight.css";

type MicrophoneState = "unchecked" | "checking" | "ready" | "error";

interface PreflightPanelProps {
  engine: string;
  translationProvider: string;
  translationModel: string;
  onApplyRecommendation: (configuration: RecommendedConfiguration) => void;
}

function checkSymbol(check: PreflightCheck) {
  if (check.status === "ready") return "✓";
  if (check.status === "warning") return "!";
  if (check.status === "info") return "i";
  return "×";
}

function providerUsesModel(provider: string) {
  return provider === "ollama" || provider === "openai-compatible";
}

export function PreflightPanel({
  engine,
  translationProvider,
  translationModel,
  onApplyRecommendation,
}: PreflightPanelProps) {
  const { locale } = useI18n();
  const copy = PREFLIGHT_COPY[locale];
  const [open, setOpen] = useState(
    () => typeof window === "undefined" || window.matchMedia("(min-width: 861px)").matches,
  );
  const [report, setReport] = useState<SystemPreflight | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [microphone, setMicrophone] = useState<MicrophoneState>("unchecked");
  const [microphoneDetail, setMicrophoneDetail] = useState(copy.initial);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        engine,
        translation_provider: translationProvider,
      });
      if (providerUsesModel(translationProvider) && translationModel.trim()) {
        query.set("translation_model", translationModel.trim());
      }
      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);
      if (!response.ok) throw new Error(`${copy.http} ${response.status}`);
      setReport((await response.json()) as SystemPreflight);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failed);
    } finally {
      setLoading(false);
    }
  }, [copy.failed, copy.http, engine, translationModel, translationProvider]);

  const handleRepairCompleted = useCallback(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 180);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (microphone === "unchecked") setMicrophoneDetail(copy.initial);
  }, [copy.initial, microphone]);

  async function checkMicrophone() {
    setMicrophone("checking");
    setMicrophoneDetail(copy.checkingMic);
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(copy.unsupported);
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((device) => device.kind === "audioinput");
        if (inputs.length === 0) throw new Error(copy.noDevice);
        setMicrophone("ready");
        setMicrophoneDetail(`${copy.found}: ${inputs.length}`);
      } finally {
        stream.getTracks().forEach((track) => track.stop());
      }
    } catch (reason) {
      setMicrophone("error");
      setMicrophoneDetail(
        reason instanceof Error ? reason.message : copy.micFailed,
      );
    }
  }

  const backendReady = report?.ready ?? false;
  const microphoneRequired = engine !== "mock";
  const fullyReady = backendReady && (!microphoneRequired || microphone === "ready");
  const headline = fullyReady
    ? copy.ready
    : backendReady && microphoneRequired
      ? copy.engineMic
      : backendReady
        ? copy.configReady
        : copy.needsCheck;

  const recommendationDiffers = useMemo(() => {
    const recommendation = report?.recommended;
    if (!recommendation?.engine) return false;
    if (recommendation.engine !== engine) return true;
    if (recommendation.translation_provider !== translationProvider) return true;
    if (providerUsesModel(recommendation.translation_provider)) {
      return recommendation.translation_model !== translationModel.trim();
    }
    return false;
  }, [engine, report, translationModel, translationProvider]);

  const ollamaReady = report?.checks.some(
    (check) => check.id === "ollama" && check.status === "ready",
  ) ?? false;
  const translationModelMissing = report?.checks.some(
    (check) => check.id === "translation-model" && check.status === "missing",
  ) ?? false;
  const canPrepareTranslationModel =
    translationProvider === "ollama"
    && Boolean(translationModel.trim())
    && ollamaReady
    && translationModelMissing;

  const whisperPackageReady = report?.checks.some(
    (check) => check.id === "faster-whisper" && check.status === "ready",
  ) ?? false;
  const whisperModelCheck = report?.checks.find(
    (check) => check.id === "faster-whisper-model",
  );
  const whisperModel = typeof whisperModelCheck?.details.model === "string"
    ? whisperModelCheck.details.model
    : "";
  const canPrepareWhisperModel =
    (engine === "auto" || engine === "faster-whisper")
    && whisperPackageReady
    && whisperModelCheck?.status === "missing"
    && Boolean(whisperModel);
  const vibevoiceRelevant = engine === "auto" || engine === "vibevoice";

  if (!open) {
    return (
      <button
        className={`preflight-pill ${backendReady ? "ready" : "attention"}`}
        onClick={() => setOpen(true)}
      >
        {copy.pill} · {backendReady ? copy.engineReady : copy.checkNeeded}
      </button>
    );
  }

  return (
    <aside className="preflight-panel" aria-label={copy.aria}>
      <div className="preflight-titlebar">
        <div>
          <small>{copy.beforeStart}</small>
          <strong>{headline}</strong>
        </div>
        <button
          className="preflight-close"
          onClick={() => setOpen(false)}
          aria-label={copy.collapse}
        >
          −
        </button>
      </div>

      {error && <div className="preflight-error">{error}</div>}
      {report && (
        <>
          <div className="preflight-system">
            <span>{report.architecture}</span>
            {report.memory_gb !== null && <span>{report.memory_gb.toFixed(1)} GB RAM</span>}
            {report.disk_free_gb !== null && <span>{report.disk_free_gb.toFixed(1)} GB free</span>}
          </div>

          {recommendationDiffers && report.recommended.engine && (
            <div className="preflight-recommendation">
              <div>
                <small>{copy.recommendation}</small>
                <strong>
                  {report.recommended.engine} · {report.recommended.translation_provider}
                  {report.recommended.translation_model
                    ? ` / ${report.recommended.translation_model}`
                    : ""}
                </strong>
              </div>
              <ul>
                {report.recommended.reasons.map((reason) => <li key={reason}>{reason}</li>)}
              </ul>
              <button onClick={() => onApplyRecommendation(report.recommended)}>
                {copy.apply}
              </button>
            </div>
          )}

          {vibevoiceRelevant && (
            <VibeVoiceLifecycleControl enabled onReady={handleRepairCompleted} />
          )}

          {canPrepareWhisperModel && (
            <div className="preflight-repair">
              <div>
                <small>{copy.fallbackPrep}</small>
                <strong>{whisperModel} · {copy.missingCache}</strong>
                <span>{copy.fallbackHelp}</span>
              </div>
              <ModelSetupControl
                provider="faster-whisper"
                model={whisperModel}
                enabled
                onCompleted={handleRepairCompleted}
              />
            </div>
          )}

          {canPrepareTranslationModel && (
            <div className="preflight-repair">
              <div>
                <small>{copy.autoRepair}</small>
                <strong>{copy.missingTranslation}</strong>
                <span>{copy.translationHelp}</span>
              </div>
              <ModelSetupControl
                provider="ollama"
                model={translationModel}
                enabled
                onCompleted={handleRepairCompleted}
              />
            </div>
          )}

          <div className="preflight-checks">
            {report.checks.map((check) => (
              <div className={`preflight-check ${check.status}`} key={check.id}>
                <i>{checkSymbol(check)}</i>
                <div>
                  <strong>{check.label}</strong>
                  <span>{check.summary}</span>
                  {check.recommendation && <small>{check.recommendation}</small>}
                </div>
              </div>
            ))}
            {microphoneRequired && (
              <div
                className={`preflight-check ${
                  microphone === "ready" ? "ready" : microphone === "error" ? "error" : "info"
                }`}
              >
                <i>{microphone === "ready" ? "✓" : microphone === "error" ? "×" : "i"}</i>
                <div>
                  <strong>{copy.microphone}</strong>
                  <span>{microphoneDetail}</span>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      <div className="preflight-actions">
        <button onClick={() => void load()} disabled={loading}>
          {loading ? copy.checking : copy.rerun}
        </button>
        {microphoneRequired && (
          <button onClick={checkMicrophone} disabled={microphone === "checking"}>
            {microphone === "checking" ? copy.checking : copy.checkMic}
          </button>
        )}
      </div>
    </aside>
  );
}
