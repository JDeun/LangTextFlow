import { useEffect, useMemo, useRef, useState } from "react";
import { API_URL } from "./api";
import {
  AudioCaptureController,
  requestAudioInputs,
  type AudioInputDevice,
} from "./audioCapture";
import { AudienceAccess } from "./AudienceAccess";
import { ContextDocumentManager } from "./ContextDocumentManager";
import { DisplaySettingsPanel } from "./DisplaySettingsPanel";
import { loadStoredDisplaySettings } from "./displaySettings";
import { GlossaryManager } from "./GlossaryManager";
import { GlossaryRecommendations } from "./GlossaryRecommendations";
import { LanguageSwitcher, useI18n, type TranslationKey } from "./i18n";
import { LANGUAGE_OPTIONS, languageLabel } from "./languages";
import { LiveCaption } from "./LiveCaption";
import { OnboardingWizard } from "./OnboardingWizard";
import { OPERATOR_COPY } from "./operatorCopy";
import { PreflightPanel } from "./PreflightPanel";
import { RuntimeMaintenancePanel } from "./RuntimeMaintenancePanel";
import { SessionHistory } from "./SessionHistory";
import { DEFAULT_KOREAN_HOTWORDS } from "./sessionDefaults";
import { TargetLanguageSelector } from "./TargetLanguageSelector";
import { TelemetryPanel } from "./TelemetryPanel";
import { useCaptionSocket } from "./useCaptionSocket";
import type {
  AudienceSessionView,
  CaptionDisplaySettings,
  ProductPreset,
  RecommendedConfiguration,
  ReferenceDocument,
  SessionState,
  SystemPreflight,
  TranscriptEvent,
} from "./types";

const PRESET_VALUES: ProductPreset[] = ["general", "church", "conference", "lecture"];

const ONBOARDING_STORAGE_KEY = "langtextflow:onboarding:v1";
const DISPLAY_SETTINGS_STORAGE_KEY = "langtextflow:display:v1";

function displayCaption(segment: TranscriptEvent | undefined, language: string, emptyText = "") {
  if (!segment) return emptyText;
  if (language === segment.source_language) return segment.text;
  return segment.translations[language] || segment.text;
}

function splitHotwords(value: string) {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinCodeFromPath(prefix: string) {
  const match = window.location.pathname.match(new RegExp(`^/${prefix}/([A-Za-z0-9]+)`));
  return match?.[1]?.toUpperCase() ?? null;
}

function defaultTargetForSource(sourceLanguage: string) {
  return sourceLanguage === "ko" ? "en" : "ko";
}

function reconcileTargets(sourceLanguage: string, targets: string[]) {
  const filtered = targets.filter((code) => code !== sourceLanguage);
  return filtered.length > 0 ? filtered : [defaultTargetForSource(sourceLanguage)];
}

function captionLanguages(sourceLanguage: string, targetLanguages: string[]) {
  return [sourceLanguage, ...targetLanguages.filter((code) => code !== sourceLanguage)];
}

function translationProviderUsesModel(provider: string) {
  return provider === "ollama" || provider === "openai-compatible";
}

function AudienceApp({ joinCode, displayMode }: { joinCode: string; displayMode: boolean }) {
  const { t } = useI18n();
  const { connected, segments, terminalError } = useCaptionSocket(
    `/ws/audience/${encodeURIComponent(joinCode)}`,
  );
  const [session, setSession] = useState<AudienceSessionView | null>(null);
  const [error, setError] = useState("");
  const queryLanguage = new URLSearchParams(window.location.search).get("lang") ?? "";
  const [language, setLanguage] = useState(queryLanguage);
  const latest = segments.at(-1);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/audience/${encodeURIComponent(joinCode)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error(t("audience.invalidSession"));
        return (await response.json()) as AudienceSessionView;
      })
      .then((data) => {
        setSession(data);
        const available = captionLanguages(data.source_language, data.target_languages);
        setLanguage((current) => {
          if (current && available.includes(current)) return current;
          return data.target_languages[0] || data.source_language;
        });
      })
      .catch((reason: Error) => setError(reason.message));
  }, [joinCode]);

  if (error || terminalError) {
    return <main className="audience-error">{error || terminalError}</main>;
  }
  if (!session || !language) {
    return <main className="audience-error">{t("audience.loading")}</main>;
  }

  const availableLanguages = captionLanguages(session.source_language, session.target_languages);
  const displaySettings = session.display_settings;

  if (displayMode) {
    const mode = new URLSearchParams(window.location.search).get("mode") ?? "projector";
    return (
      <main className={`display-shell ${mode}`}>
        <LiveCaption
          segment={latest}
          language={language}
          settings={displaySettings}
          surface="display"
          primaryClassName="display-caption"
          sourceClassName="display-source"
        />
      </main>
    );
  }

  return (
    <main className="audience-shell">
      <header className="audience-header">
        <div>
          <div className="brand">{session.title}</div>
          <div className="subtitle">{session.presenter || "LangTextFlow Live"}</div>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="dot" /> {connected ? "LIVE" : t("audience.connecting")}
        </div>
      </header>
      <label className="audience-language">
        {t("audience.captionLanguage")}
        <select value={language} onChange={(event) => setLanguage(event.target.value)}>
          {availableLanguages.map((code) => (
            <option key={code} value={code}>
              {languageLabel(code)}{code === session.source_language ? ` · ${t("audience.source")}` : ""}
            </option>
          ))}
        </select>
      </label>
      <section className="audience-caption-card">
        <LiveCaption
          segment={latest}
          language={language}
          settings={displaySettings}
          surface="audience"
          primaryClassName="audience-caption"
          sourceClassName="audience-source"
        />
      </section>
      <section className="audience-transcript">
        {segments.slice(-8).reverse().map((segment) => (
          <article key={segment.segment_id}>
            <span>{displayCaption(segment, language, t("live.emptyCaption"))}</span>
            {segment.stage === "partial" && <small>draft</small>}
          </article>
        ))}
      </section>
    </main>
  );
}

function OperatorApp() {
  const { locale, t } = useI18n();
  const operatorCopy = OPERATOR_COPY[locale];
  const { connected, segments, terminalError } = useCaptionSocket();
  const captureRef = useRef<AudioCaptureController | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("ko");
  const [targetLanguages, setTargetLanguages] = useState<string[]>(["en"]);
  const [previewLanguage, setPreviewLanguage] = useState("en");
  const [displaySettings, setDisplaySettings] = useState<CaptionDisplaySettings>(
    () => loadStoredDisplaySettings(DISPLAY_SETTINGS_STORAGE_KEY),
  );
  const [title, setTitle] = useState(() => t("session.newTitle"));
  const [presenter, setPresenter] = useState("");
  const [preset, setPreset] = useState<ProductPreset>("church");
  const [hotwords, setHotwords] = useState(DEFAULT_KOREAN_HOTWORDS);
  const [referenceDocuments, setReferenceDocuments] = useState<ReferenceDocument[]>([]);
  const [engine, setEngine] = useState("auto");
  const [correctionProvider, setCorrectionProvider] = useState("none");
  const [correctionModel, setCorrectionModel] = useState("qwen3.5:4b");
  const [translationProvider, setTranslationProvider] = useState("ollama");
  const [translationModel, setTranslationModel] = useState("translategemma:4b");
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [session, setSession] = useState<SessionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [onboardingOpen, setOnboardingOpen] = useState(
    () => window.localStorage.getItem(ONBOARDING_STORAGE_KEY) !== "complete",
  );

  const running = session?.running ?? false;
  const latest = segments.at(-1);
  const recent = useMemo(() => segments.slice(-6).reverse(), [segments]);
  const availablePreviewLanguages = useMemo(
    () => captionLanguages(sourceLanguage, targetLanguages),
    [sourceLanguage, targetLanguages],
  );
  const historyRefreshToken = `${session?.session_id ?? "none"}:${running}`;
  const needsAudio = engine !== "mock";
  const glossaryTargetLanguage = targetLanguages.includes(previewLanguage)
    ? previewLanguage
    : targetLanguages[0] || defaultTargetForSource(sourceLanguage);

  useEffect(() => {
    if (!availablePreviewLanguages.includes(previewLanguage)) {
      setPreviewLanguage(targetLanguages[0] || sourceLanguage);
    }
  }, [availablePreviewLanguages, previewLanguage, sourceLanguage, targetLanguages]);

  useEffect(() => {
    window.localStorage.setItem(
      DISPLAY_SETTINGS_STORAGE_KEY,
      JSON.stringify(displaySettings),
    );
  }, [displaySettings]);

  function changeSourceLanguage(nextSource: string) {
    const nextTargets = reconcileTargets(nextSource, targetLanguages);
    setSourceLanguage(nextSource);
    setTargetLanguages(nextTargets);
    setPreviewLanguage(nextTargets[0] || defaultTargetForSource(nextSource));
  }

  function changeTargetLanguages(nextTargets: string[]) {
    const reconciled = reconcileTargets(sourceLanguage, nextTargets);
    setTargetLanguages(reconciled);
    if (!captionLanguages(sourceLanguage, reconciled).includes(previewLanguage)) {
      setPreviewLanguage(reconciled[0] || sourceLanguage);
    }
  }

  function changeEngine(nextEngine: string) {
    setEngine(nextEngine);
    setTranslationProvider((current) => {
      if (nextEngine === "mock") return "demo";
      return current === "demo" ? "ollama" : current;
    });
  }

  function applyRecommendation(configuration: RecommendedConfiguration) {
    if (configuration.engine) setEngine(configuration.engine);
    setTranslationProvider(configuration.translation_provider);
    if (configuration.translation_model) {
      setTranslationModel(configuration.translation_model);
    }
  }

  function completeOnboarding() {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "complete");
    setOnboardingOpen(false);
  }

  async function refreshDevices() {
    setError("");
    try {
      const result = await requestAudioInputs();
      setDevices(result);
      setDeviceId((current) => current || result[0]?.deviceId || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("audio.deviceError"));
    }
  }

  async function assertPreflightReady() {
    const query = new URLSearchParams({
      engine,
      translation_provider: translationProvider,
    });
    if (translationProviderUsesModel(translationProvider) && translationModel.trim()) {
      query.set("translation_model", translationModel.trim());
    }
    const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `${t("preflight.requestFailed")} (${response.status})`);
    }
    const report = (await response.json()) as SystemPreflight;
    if (!report.ready) {
      const labels = report.blocking_checks
        .map((checkId) => report.checks.find((check) => check.id === checkId)?.label || checkId)
        .join(", ");
      throw new Error(`${t("preflight.needsSetup")}: ${labels || t("preflight.checkSystem")}`);
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      await assertPreflightReady();

      let selectedDevice = deviceId;
      if (needsAudio && !selectedDevice) {
        const result = await requestAudioInputs();
        setDevices(result);
        selectedDevice = result[0]?.deviceId || "";
        setDeviceId(selectedDevice);
        if (!selectedDevice) throw new Error(t("audio.noDevice"));
      }

      const response = await fetch(`${API_URL}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_language: sourceLanguage,
          target_languages: targetLanguages,
          engine,
          correction_provider: correctionProvider,
          correction_model: correctionProvider === "ollama" ? correctionModel : null,
          translation_provider: translationProvider,
          translation_model: translationProviderUsesModel(translationProvider)
            ? translationModel
            : null,
          context: {
            title,
            presenter: presenter || null,
            preset,
            description: "",
            hotwords: splitHotwords(hotwords),
            glossary: [],
            reference_documents: referenceDocuments,
            reference_text: "",
            output_modes: ["audience", "projector", "obs"],
            audience_access: true,
            display_settings: displaySettings,
          },
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const nextSession = (await response.json()) as SessionState;
      setSession(nextSession);
      if (nextSession.context?.reference_documents) {
        setReferenceDocuments(nextSession.context.reference_documents);
      }

      if (nextSession.audio_required) {
        const capture = new AudioCaptureController((message) => {
          setError(message);
          void (async () => {
            await capture.stop().catch(() => undefined);
            if (captureRef.current === capture) captureRef.current = null;
            const stopResponse = await fetch(`${API_URL}/api/v1/session/stop`, {
              method: "POST",
            }).catch(() => null);
            if (stopResponse?.ok) {
              setSession((await stopResponse.json()) as SessionState);
            }
          })();
        });
        captureRef.current = capture;
        await capture.start(selectedDevice);
      }
    } catch (reason) {
      await captureRef.current?.stop();
      captureRef.current = null;
      await fetch(`${API_URL}/api/v1/session/stop`, { method: "POST" }).catch(() => undefined);
      setSession(null);
      setError(reason instanceof Error ? reason.message : t("error.startFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError("");
    try {
      await captureRef.current?.stop();
      captureRef.current = null;
      const response = await fetch(`${API_URL}/api/v1/session/stop`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      setSession((await response.json()) as SessionState);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("error.stopFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <OnboardingWizard
        open={onboardingOpen && !running}
        engine={engine}
        translationProvider={translationProvider}
        translationModel={translationModel}
        sourceLanguage={sourceLanguage}
        targetLanguages={targetLanguages}
        preset={preset}
        onEngineChange={changeEngine}
        onTranslationProviderChange={setTranslationProvider}
        onTranslationModelChange={setTranslationModel}
        onSourceLanguageChange={changeSourceLanguage}
        onTargetLanguagesChange={changeTargetLanguages}
        onPresetChange={setPreset}
        onApplyRecommendation={applyRecommendation}
        onComplete={completeOnboarding}
        onClose={() => setOnboardingOpen(false)}
      />

      <header className="topbar">
        <div>
          <div className="brand">LangTextFlow</div>
          <div className="subtitle">{t("app.tagline")}</div>
        </div>
        <nav className="product-nav" aria-label="Workspace">
          <button type="button" onClick={() => document.getElementById("live-workspace")?.scrollIntoView({ behavior: "smooth" })}>{t("app.live")}</button>
          <button type="button" onClick={() => document.getElementById("setup-workspace")?.scrollIntoView({ behavior: "smooth" })}>{t("app.setup")}</button>
          <button type="button" onClick={() => document.getElementById("history-workspace")?.scrollIntoView({ behavior: "smooth" })}>{t("app.history")}</button>
        </nav>
        <div className="topbar-actions">
          <LanguageSwitcher compact />
          <button
            className="secondary-button setup-button"
            onClick={() => setOnboardingOpen(true)}
            disabled={running}
          >
            {t("app.setup")}
          </button>
          <div data-testid="connection-status" className={`connection ${connected ? "online" : "offline"}`}>
            <span className="dot" /> {connected ? t("app.serverConnected") : t("app.serverConnecting")}
          </div>
        </div>
      </header>

      <div className="workspace">
        <aside id="setup-workspace" className="control-panel panel">
          <div className="section-heading">
            <span>{t("session.settings")}</span>
            <span className="beta">P3C</span>
          </div>

          <label>
            {t("session.title")}
            <input value={title} onChange={(event) => setTitle(event.target.value)} disabled={running} />
          </label>
          <label>
            {t("session.presenter")}
            <input
              value={presenter}
              onChange={(event) => setPresenter(event.target.value)}
              disabled={running}
              placeholder={t("session.optional")}
            />
          </label>
          <label>
            {t("session.preset")}
            <select
              value={preset}
              onChange={(event) => setPreset(event.target.value as ProductPreset)}
              disabled={running}
            >
              {PRESET_VALUES.map((value) => (
                <option value={value} key={value}>{t(`preset.${value}` as TranslationKey)}</option>
              ))}
            </select>
          </label>
          <label>
            {t("session.sourceLanguage")}
            <select
              value={sourceLanguage}
              onChange={(event) => changeSourceLanguage(event.target.value)}
              disabled={running}
            >
              {LANGUAGE_OPTIONS.map(([code, label]) => (
                <option value={code} key={code}>{label}</option>
              ))}
            </select>
            <small>{t("session.sourceHelp")}</small>
          </label>
          <TargetLanguageSelector
            value={targetLanguages}
            sourceLanguage={sourceLanguage}
            disabled={running}
            onChange={changeTargetLanguages}
          />
          <DisplaySettingsPanel
            value={displaySettings}
            disabled={running}
            onChange={setDisplaySettings}
          />
          <details className="settings-group">
            <summary>
              <span><strong>{operatorCopy.contextGroup}</strong><small>{operatorCopy.contextGroupHelp}</small></span>
            </summary>
            <div className="settings-group-body">
          <label>
            {t("session.hotwords")}
            <textarea
              rows={4}
              value={hotwords}
              onChange={(event) => setHotwords(event.target.value)}
              disabled={running}
            />
            <small>{t("session.hotwordsHelp")}</small>
          </label>

          <ContextDocumentManager
            value={referenceDocuments}
            disabled={running}
            onChange={setReferenceDocuments}
          />

          <GlossaryManager
            apiUrl={API_URL}
            preset={preset}
            targetLanguage={glossaryTargetLanguage}
            disabled={running}
          />
          <GlossaryRecommendations preset={preset} disabled={running} />
            </div>
          </details>

          <details className="settings-group">
            <summary>
              <span><strong>{operatorCopy.advancedGroup}</strong><small>{operatorCopy.advancedGroupHelp}</small></span>
            </summary>
            <div className="settings-group-body">
          <label>
            {t("engines.asr")}
            <select
              data-testid="engine-select"
              value={engine}
              onChange={(event) => changeEngine(event.target.value)}
              disabled={running}
            >
              <option value="auto">Auto · VibeVoice → faster-whisper</option>
              <option value="vibevoice">VibeVoice Streaming (local sidecar)</option>
              <option value="faster-whisper">faster-whisper (local fallback)</option>
              <option value="mock">Demo engine</option>
            </select>
            {engine === "auto" && (
              <small>{t("engines.autoHelp")}</small>
            )}
          </label>
          <label>
            {t("engines.correction")}
            <select
              value={correctionProvider}
              onChange={(event) => setCorrectionProvider(event.target.value)}
              disabled={running}
            >
              <option value="none">{operatorCopy.rulesOnly}</option>
              <option value="ollama">Ollama constrained correction (local)</option>
            </select>
            <small>{t("engines.correctionHelp")}</small>
          </label>
          {correctionProvider === "ollama" && (
            <label>
              {t("engines.correctionModel")}
              <input
                value={correctionModel}
                onChange={(event) => setCorrectionModel(event.target.value)}
                disabled={running}
              />
              <small>{operatorCopy.correctionDefaultHelp}</small>
            </label>
          )}
          <label>
            {t("engines.translation")}
            <select
              value={translationProvider}
              onChange={(event) => setTranslationProvider(event.target.value)}
              disabled={running}
            >
              {engine === "mock" && <option value="demo">Demo translator</option>}
              <option value="ollama">Ollama (local)</option>
              <option value="openai-compatible">OpenAI-compatible API</option>
              <option value="none">{operatorCopy.noTranslation}</option>
            </select>
          </label>
          {translationProviderUsesModel(translationProvider) && (
            <label>
              {translationProvider === "ollama" ? t("engines.translationModel") : t("engines.apiModel")}
              <input
                value={translationModel}
                onChange={(event) => setTranslationModel(event.target.value)}
                disabled={running}
              />
              <small>
                {translationProvider === "ollama"
                  ? operatorCopy.translationRecommended
                  : operatorCopy.exactModelId}
              </small>
            </label>
          )}
          <RuntimeMaintenancePanel disabled={running} />
            </div>
          </details>

          {needsAudio && (
            <div className="device-block">
              <label>
                {t("audio.input")}
                <select
                  value={deviceId}
                  onChange={(event) => setDeviceId(event.target.value)}
                  disabled={running}
                >
                  <option value="">
                    {devices.length ? t("audio.defaultDevice") : t("audio.findDevice")}
                  </option>
                  {devices.map((device) => (
                    <option value={device.deviceId} key={device.deviceId}>{device.label}</option>
                  ))}
                </select>
              </label>
              <button
                className="secondary-button device-button"
                onClick={refreshDevices}
                disabled={running || busy}
              >
                {t("audio.refresh")}
              </button>
            </div>
          )}

          {(error || terminalError) && (
            <div className="error-box">{error || terminalError}</div>
          )}
          {session?.persistence_error && (
            <div className="warning-box">{t("live.persistenceWarning")}: {session.persistence_error}</div>
          )}
          <button
            className={running ? "stop-button" : "start-button"}
            data-testid="session-toggle"
            onClick={running ? stop : start}
            disabled={busy || !connected}
          >
            {busy ? t("session.busy") : running ? t("session.stop") : t("session.start")}
          </button>

          <div className="pipeline-card">
            <span>{t("live.status")}</span>
            <strong>{latest?.stage ?? "idle"}</strong>
            <div className="stage-track">
              {["partial", "stable", "corrected", "translated", "committed"].map((stage) => (
                <i key={stage} className={latest?.stage === stage ? "active" : ""} title={stage} />
              ))}
            </div>
            {session?.audio_sample_rate && (
              <small>{session.engine} · {session.audio_sample_rate} Hz</small>
            )}
            {session?.correction_status.enabled && (
              <small>
                {operatorCopy.correctionStatus}: {session.correction_status.provider} / {session.correction_status.model || "default"}
                {session.correction_status.available
                  ? ` · ${operatorCopy.ready}`
                  : ` · ${operatorCopy.deterministicFallback}: ${session.correction_status.error || operatorCopy.unavailable}`}
              </small>
            )}
            {session?.translation_status.enabled && (
              <small>
                {operatorCopy.translationStatus}: {session.translation_status.provider} / {session.translation_status.model || "default"}
                {session.translation_status.available
                  ? ` · ${operatorCopy.ready}`
                  : ` · ${operatorCopy.unavailable}: ${session.translation_status.error || operatorCopy.unknown}`}
              </small>
            )}
          </div>

          <TelemetryPanel apiUrl={API_URL} running={running} />
        </aside>

        <section id="live-workspace" className="main-column">
          {session?.join_code && (
            <AudienceAccess joinCode={session.join_code} targetLanguage={previewLanguage} />
          )}

          <div className="preview panel">
            <div className="preview-toolbar">
              <span>{t("live.preview")}</span>
              <label>
                {t("live.displayLanguage")}
                <select
                  value={previewLanguage}
                  onChange={(event) => setPreviewLanguage(event.target.value)}
                >
                  {availablePreviewLanguages.map((code) => (
                    <option key={code} value={code}>
                      {languageLabel(code)}{code === sourceLanguage ? ` · ${t("audience.source")}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="stage-screen">
              <LiveCaption
                segment={latest}
                language={previewLanguage}
                settings={displaySettings}
                surface="preview"
                primaryClassName="caption"
                sourceClassName="source-caption"
                emptyText={t("live.emptyCaption")}
              />
            </div>
          </div>

          <div className="transcript panel">
            <div className="section-heading">{t("live.segments")}</div>
            <div className="transcript-list">
              {recent.length === 0 && (
                <div className="empty">{t("live.emptySegments")}</div>
              )}
              {recent.map((segment) => (
                <article className="segment" key={segment.segment_id}>
                  <div className="segment-meta">
                    <span>{segment.segment_id}</span>
                    <span className={`stage-tag ${segment.stage}`}>{segment.stage}</span>
                    <span>v{segment.version}</span>
                  </div>
                  <div className="segment-text">{displayCaption(segment, previewLanguage, t("live.emptyCaption"))}</div>
                  {previewLanguage !== sourceLanguage && segment.translations[previewLanguage] && (
                    <div className="segment-source">{segment.text}</div>
                  )}
                </article>
              ))}
            </div>
          </div>

          <div id="history-workspace" className="history-anchor">
          <SessionHistory
            apiUrl={API_URL}
            targetLanguage={previewLanguage}
            activeSessionId={running ? session?.session_id ?? null : null}
            refreshToken={historyRefreshToken}
          />
          </div>
        </section>
      </div>

      {!running && (
        <PreflightPanel
          engine={engine}
          translationProvider={translationProvider}
          translationModel={translationModel}
          onApplyRecommendation={applyRecommendation}
        />
      )}
    </main>
  );
}

export default function App() {
  const audienceCode = joinCodeFromPath("audience");
  if (audienceCode) return <AudienceApp joinCode={audienceCode} displayMode={false} />;
  const displayCode = joinCodeFromPath("display");
  if (displayCode) return <AudienceApp joinCode={displayCode} displayMode />;
  return <OperatorApp />;
}
