import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { useI18n, type TranslationKey } from "./i18n";
import { LANGUAGE_OPTIONS, languageLabel } from "./languages";
import { PANEL_COPY } from "./panelCopy";
import { ModelSetupControl } from "./ModelSetupControl";
import { TargetLanguageSelector } from "./TargetLanguageSelector";
import type { ProductPreset, RecommendedConfiguration, SystemPreflight } from "./types";
import { VibeVoiceLifecycleControl } from "./VibeVoiceLifecycleControl";
import "./onboarding.css";

const PRESET_VALUES: ProductPreset[] = ["general", "church", "conference", "lecture"];

function providerUsesModel(provider: string) {
  return provider === "ollama" || provider === "openai-compatible";
}

interface OnboardingWizardProps {
  open: boolean;
  engine: string;
  translationProvider: string;
  translationModel: string;
  sourceLanguage: string;
  targetLanguages: string[];
  preset: ProductPreset;
  onEngineChange: (value: string) => void;
  onTranslationProviderChange: (value: string) => void;
  onTranslationModelChange: (value: string) => void;
  onSourceLanguageChange: (value: string) => void;
  onTargetLanguagesChange: (value: string[]) => void;
  onPresetChange: (value: ProductPreset) => void;
  onApplyRecommendation: (configuration: RecommendedConfiguration) => void;
  onComplete: () => void;
  onClose: () => void;
}

type MicrophoneState = "unchecked" | "checking" | "ready" | "error";

export function OnboardingWizard({
  open,
  engine,
  translationProvider,
  translationModel,
  sourceLanguage,
  targetLanguages,
  preset,
  onEngineChange,
  onTranslationProviderChange,
  onTranslationModelChange,
  onSourceLanguageChange,
  onTargetLanguagesChange,
  onPresetChange,
  onApplyRecommendation,
  onComplete,
  onClose,
}: OnboardingWizardProps) {
  const { locale, t } = useI18n();
  const copy = PANEL_COPY[locale].onboarding;
  const [step, setStep] = useState(0);
  const [report, setReport] = useState<SystemPreflight | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [microphone, setMicrophone] = useState<MicrophoneState>("unchecked");
  const [microphoneDetail, setMicrophoneDetail] = useState(copy.micInitialDetail);
  const preflightRequestRef = useRef(0);

  const loadPreflight = useCallback(async () => {
    const requestId = ++preflightRequestRef.current;
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
      if (!response.ok) throw new Error(`${copy.systemHttp} ${response.status}`);
      const nextReport = (await response.json()) as SystemPreflight;
      if (requestId === preflightRequestRef.current) setReport(nextReport);
    } catch (reason) {
      if (requestId === preflightRequestRef.current) {
        setError(reason instanceof Error ? reason.message : copy.systemFailed);
      }
    } finally {
      if (requestId === preflightRequestRef.current) setLoading(false);
    }
  }, [copy.systemFailed, copy.systemHttp, engine, translationModel, translationProvider]);

  const handleRepairCompleted = useCallback(() => {
    void loadPreflight();
  }, [loadPreflight]);

  useEffect(() => {
    if (!open || step !== 1) return;
    void loadPreflight();
  }, [loadPreflight, open, step]);

  useEffect(() => {
    if (open) {
      setStep(0);
    } else {
      preflightRequestRef.current += 1;
      setLoading(false);
    }
  }, [open]);

  useEffect(() => {
    if (microphone === "unchecked") setMicrophoneDetail(copy.micInitialDetail);
  }, [copy.micInitialDetail, microphone]);

  if (!open) return null;

  async function checkMicrophone() {
    setMicrophone("checking");
    setMicrophoneDetail(copy.micCheckingDetail);
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(copy.micUnsupported);
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const inputs = devices.filter((device) => device.kind === "audioinput");
        if (inputs.length === 0) throw new Error(copy.micNoDevice);
        setMicrophone("ready");
        setMicrophoneDetail(`${copy.micFoundPrefix}: ${inputs.length}`);
      } finally {
        stream.getTracks().forEach((track) => track.stop());
      }
    } catch (reason) {
      setMicrophone("error");
      setMicrophoneDetail(
        reason instanceof Error ? reason.message : copy.micGenericFailed,
      );
    }
  }

  function finish() {
    onComplete();
    setStep(0);
  }

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
  const steps = copy.steps;

  return (
    <div className="onboarding-backdrop" role="presentation">
      <section className="onboarding-dialog" role="dialog" aria-modal="true" aria-label={copy.aria}>
        <header className="onboarding-header">
          <div>
            <small>{copy.title}</small>
            <strong>{steps[step]}</strong>
          </div>
          <button onClick={onClose} aria-label={copy.close}>×</button>
        </header>

        <div className="onboarding-progress" aria-label={copy.progress}>
          {steps.map((label, index) => (
            <i
              key={label}
              className={index <= step ? "active" : ""}
              title={label}
            />
          ))}
        </div>

        <div className="onboarding-body">
          {step === 0 && (
            <div className="onboarding-copy">
              <h2>{copy.introTitle}</h2>
              <p>{copy.introBody}</p>
              <div className="onboarding-note">{copy.introNote}</div>
            </div>
          )}

          {step === 1 && (
            <div className="onboarding-copy">
              <h2>{copy.systemTitle}</h2>
              {loading && <p>{copy.systemChecking}</p>}
              {error && <div className="onboarding-error">{error}</div>}
              {report && (
                <>
                  <div className={`onboarding-status ${report.ready ? "ready" : "attention"}`}>
                    <strong>{report.ready ? copy.ready : copy.attention}</strong>
                    <span>
                      {report.architecture}
                      {report.memory_gb !== null ? ` · ${report.memory_gb.toFixed(1)} GB RAM` : ""}
                    </span>
                  </div>
                  {report.recommended.engine && (
                    <div className="onboarding-recommendation">
                      <small>{copy.recommended}</small>
                      <strong>
                        {report.recommended.engine} · {report.recommended.translation_provider}
                        {report.recommended.translation_model
                          ? ` / ${report.recommended.translation_model}`
                          : ""}
                      </strong>
                      <ul>
                        {report.recommended.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                      </ul>
                      <button onClick={() => onApplyRecommendation(report.recommended)}>
                        {copy.applyRecommended}
                      </button>
                    </div>
                  )}
                  {vibevoiceRelevant && (
                    <VibeVoiceLifecycleControl enabled onReady={handleRepairCompleted} />
                  )}
                  {canPrepareWhisperModel && (
                    <div className="onboarding-repair">
                      <div>
                        <small>{copy.fallbackPrep}</small>
                        <strong>{whisperModel} {copy.fallbackModel}</strong>
                        <span>{copy.fallbackDetail}</span>
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
                    <div className="onboarding-repair">
                      <div>
                        <small>{copy.translationPrep}</small>
                        <strong>{copy.translationPrepTitle}</strong>
                        <span>{copy.translationPrepDetail}</span>
                      </div>
                      <ModelSetupControl
                        provider="ollama"
                        model={translationModel}
                        enabled
                        onCompleted={handleRepairCompleted}
                      />
                    </div>
                  )}
                  <div className="onboarding-check-list">
                    {report.checks.map((check) => (
                      <div key={check.id} className={check.status}>
                        <b>{check.status === "ready" ? "✓" : check.status === "warning" ? "!" : "×"}</b>
                        <span>
                          <strong>{check.label}</strong>
                          <small>{check.summary}</small>
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="onboarding-copy">
              <h2>{copy.micTitle}</h2>
              <p>{copy.micBody}</p>
              <div className={`onboarding-status ${microphone === "ready" ? "ready" : microphone === "error" ? "attention" : ""}`}>
                <strong>
                  {microphone === "ready"
                    ? copy.micReady
                    : microphone === "checking"
                      ? copy.micChecking
                      : microphone === "error"
                        ? copy.micFailed
                        : copy.micUnchecked}
                </strong>
                <span>{microphoneDetail}</span>
              </div>
              <button className="onboarding-primary" onClick={checkMicrophone} disabled={microphone === "checking"}>
                {microphone === "checking" ? `${copy.micChecking}…` : copy.micAction}
              </button>
            </div>
          )}

          {step === 3 && (
            <div className="onboarding-copy">
              <h2>{copy.basicsTitle}</h2>
              <div className="onboarding-grid">
                <label>
                  {copy.sourceLanguage}
                  <select value={sourceLanguage} onChange={(event) => onSourceLanguageChange(event.target.value)}>
                    {LANGUAGE_OPTIONS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                  </select>
                </label>
                <label>
                  {copy.useCase}
                  <select value={preset} onChange={(event) => onPresetChange(event.target.value as ProductPreset)}>
                    {PRESET_VALUES.map((value) => (
                    <option key={value} value={value}>{t(`preset.${value}` as TranslationKey)}</option>
                  ))}
                  </select>
                </label>
                <label>
                  {copy.speechRecognition}
                  <select value={engine} onChange={(event) => onEngineChange(event.target.value)}>
                    <option value="auto">Auto</option>
                    <option value="vibevoice">VibeVoice Streaming</option>
                    <option value="faster-whisper">faster-whisper</option>
                    <option value="mock">Demo engine</option>
                  </select>
                </label>
                <label>
                  {copy.translation}
                  <select
                    value={translationProvider}
                    onChange={(event) => onTranslationProviderChange(event.target.value)}
                  >
                    <option value="ollama">Ollama</option>
                    <option value="openai-compatible">OpenAI-compatible API</option>
                    <option value="none">{copy.noTranslation}</option>
                    {engine === "mock" && <option value="demo">Demo translator</option>}
                  </select>
                </label>
                {providerUsesModel(translationProvider) && (
                  <label>
                    {copy.translationModel}
                    <input
                      value={translationModel}
                      onChange={(event) => onTranslationModelChange(event.target.value)}
                    />
                    {translationProvider === "openai-compatible" && (
                      <small>{copy.exactModelId}</small>
                    )}
                  </label>
                )}
              </div>
              <div className="onboarding-target-languages">
                <TargetLanguageSelector
                  value={targetLanguages}
                  sourceLanguage={sourceLanguage}
                  onChange={onTargetLanguagesChange}
                  compact
                />
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="onboarding-copy onboarding-finish">
              <div className="onboarding-finish-mark">✓</div>
              <h2>{copy.finishedTitle}</h2>
              <p>{copy.finishedBody}</p>
              <dl>
                <div><dt>{copy.speechRecognition}</dt><dd>{engine}</dd></div>
                <div><dt>{copy.translation}</dt><dd>{translationProvider}</dd></div>
                <div>
                  <dt>{copy.language}</dt>
                  <dd>{languageLabel(sourceLanguage)} → {targetLanguages.map(languageLabel).join(", ")}</dd>
                </div>
              </dl>
            </div>
          )}
        </div>

        <footer className="onboarding-footer">
          <button onClick={step === 0 ? onClose : () => setStep((value) => Math.max(0, value - 1))}>
            {step === 0 ? copy.later : copy.previous}
          </button>
          {step < 4 ? (
            <button className="onboarding-primary" onClick={() => setStep((value) => Math.min(4, value + 1))}>
              {copy.next}
            </button>
          ) : (
            <button className="onboarding-primary" onClick={finish}>{copy.begin}</button>
          )}
        </footer>
      </section>
    </div>
  );
}
