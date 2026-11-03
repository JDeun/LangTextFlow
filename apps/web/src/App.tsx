import { useEffect, useMemo, useState } from "react";
import { API_URL } from "./api";
import { installCaptionRenderCollector } from "./captionRenderMetrics";
import { AudienceApp } from "./AudienceApp";
import { useOperatorSession } from "./useOperatorSession";
import { AudienceAccess } from "./AudienceAccess";
import { AsrProviderSelect } from "./AsrProviderSelect";
import { useAsrCapabilities } from "./useAsrCapabilities";
import { ContextDocumentManager } from "./ContextDocumentManager";
import { captionTextForLanguage as displayCaption } from "./captionLanguage";
import { DisplaySettingsPanel } from "./DisplaySettingsPanel";
import { loadStoredDisplaySettings } from "./displaySettings";
import { GlossaryManager } from "./GlossaryManager";
import { GLOSSARY_COPY } from "./glossaryCopy";
import { GlossaryRecommendations } from "./GlossaryRecommendations";
import { LanguageSwitcher, useI18n, type TranslationKey } from "./i18n";
import { captionLanguages, LANGUAGE_OPTIONS, languageLabel } from "./languages";
import { LiveCaption } from "./LiveCaption";
import { OnboardingWizard } from "./OnboardingWizard";
import { OPERATOR_COPY } from "./operatorCopy";
import { RECORDING_COPY } from "./recordingCopy";
import { AUDIENCE_METRICS_COPY } from "./audienceMetricsCopy";
import { PreflightPanel } from "./PreflightPanel";
import { RuntimeMaintenancePanel } from "./RuntimeMaintenancePanel";
import { SessionHistory } from "./SessionHistory";
import { DEFAULT_KOREAN_HOTWORDS } from "./sessionDefaults";
import { translationProviderUsesModel } from "./sessionStartPayload";
import { TargetLanguageSelector } from "./TargetLanguageSelector";
import { TranslationProviderSelect } from "./TranslationProviderSelect";
import { useTranslationCapabilities } from "./useTranslationCapabilities";
import { TelemetryPanel } from "./TelemetryPanel";
import { useCaptionSocket } from "./useCaptionSocket";
import type {
  CaptionDisplaySettings,
  EventProject,
  ProductPreset,
  RecommendedConfiguration,
  ReferenceDocument,
} from "./types";

const PRESET_VALUES: ProductPreset[] = ["general", "church", "conference", "lecture"];

const ONBOARDING_STORAGE_KEY = "langtextflow:onboarding:v1";
const DISPLAY_SETTINGS_STORAGE_KEY = "langtextflow:display:v1";

// One collector per tab; mounted independently of operator/audience route changes.
if (typeof window !== "undefined") {
  const globalWithCollector = window as Window & { __ltfCaptionCollectorInstalled?: boolean };
  if (!globalWithCollector.__ltfCaptionCollectorInstalled) {
    installCaptionRenderCollector(window);
    globalWithCollector.__ltfCaptionCollectorInstalled = true;
  }
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

function OperatorApp() {
  const { locale, t } = useI18n();
  const operatorCopy = OPERATOR_COPY[locale];
  const glossaryCopy = GLOSSARY_COPY[locale];
  const recordingCopy = RECORDING_COPY[locale];
  const { connected, segments, terminalError } = useCaptionSocket();
  const {
    session, running, busy, error, devices, deviceId, setDeviceId,
    recordAudio, setRecordAudio, collectAudienceMetrics, setCollectAudienceMetrics,
    refreshDevices, start: startSession, stop,
  } = useOperatorSession();
  const [sourceLanguage, setSourceLanguage] = useState("ko");
  const [targetLanguages, setTargetLanguages] = useState<string[]>(["en"]);
  const [previewLanguage, setPreviewLanguage] = useState("en");
  const [displaySettings, setDisplaySettings] = useState<CaptionDisplaySettings>(
    () => loadStoredDisplaySettings(DISPLAY_SETTINGS_STORAGE_KEY),
  );
  const [title, setTitle] = useState(() => t("session.newTitle"));
  const [presenter, setPresenter] = useState("");
  const [preset, setPreset] = useState<ProductPreset>("church");
  // Manual hotwords are source-language-specific: do not send Korean hints for EN/JA speech.
  const [hotwordsByLanguage, setHotwordsByLanguage] = useState<Record<string, string>>(
    { ko: DEFAULT_KOREAN_HOTWORDS },
  );
  const hotwords = hotwordsByLanguage[sourceLanguage] ?? "";
  const [glossaryProjectId, setGlossaryProjectId] = useState("global");
  const [workspacePage, setWorkspacePage] = useState<"home" | "glossary">(
    () => window.location.hash === "#/glossary" ? "glossary" : "home",
  );
  useEffect(() => {
    const onPopState = () => {
      setWorkspacePage(window.location.hash === "#/glossary" ? "glossary" : "home");
    };
    window.addEventListener("popstate", onPopState);
    window.addEventListener("hashchange", onPopState);
    return () => {
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("hashchange", onPopState);
    };
  }, []);
  function switchWorkspace(page: "home" | "glossary") {
    // Hash routing also works for packaged Tauri/file:// assets without rewrites.
    const hash = page === "glossary" ? "#/glossary" : "";
    if (window.location.hash !== hash) {
      window.history.pushState({}, "", `${window.location.pathname}${window.location.search}${hash}`);
    }
    setWorkspacePage(page);
  }
  const [referenceDocuments, setReferenceDocuments] = useState<ReferenceDocument[]>([]);
  const [engine, setEngine] = useState("auto");
  const asrRegistry = useAsrCapabilities();
  const [resourceProfile, setResourceProfile] = useState<"standard" | "low">("standard");
  const [correctionProvider, setCorrectionProvider] = useState("none");
  const [correctionModel, setCorrectionModel] = useState("");
  const [translationProvider, setTranslationProvider] = useState("ollama");
  const translationRegistry = useTranslationCapabilities();
  const [translationModels, setTranslationModels] = useState<Record<string, string>>({});
  const translationModel = translationModels[translationProvider] ?? "";
  function setTranslationModel(value: string) {
    setTranslationModels(current => ({ ...current, [translationProvider]: value }));
  }
  const [projects, setProjects] = useState<EventProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("all");
  const [projectLoadError, setProjectLoadError] = useState("");
  const [onboardingOpen, setOnboardingOpen] = useState(
    () => window.localStorage.getItem(ONBOARDING_STORAGE_KEY) !== "complete",
  );

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

  async function refreshProjects(): Promise<void> {
    const response = await fetch(`${API_URL}/api/v1/projects`);
    if (!response.ok) throw new Error("Event project list unavailable");
    const records = (await response.json()) as EventProject[];
    setProjects(records);
    setProjectLoadError("");
  }

  useEffect(() => {
    void refreshProjects().catch((reason: Error) => setProjectLoadError(reason.message));
  }, [historyRefreshToken]);

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

  function changeResourceProfile(value: "standard" | "low") {
    setResourceProfile(value);
    if (value === "low") {
      // ASR is CPU-only; auxiliary local 4B LLMs are explicitly opt-in.
      setEngine("auto");
      setCorrectionProvider("none");
      setTranslationProvider("none");
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
    setTranslationModels(current => ({
      ...current, [configuration.translation_provider]: configuration.translation_model ?? "",
    }));
  }

  function completeOnboarding() {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "complete");
    setOnboardingOpen(false);
  }

  async function start() {
    const nextSession = await startSession({
      selectedProjectId, sourceLanguage, targetLanguages, engine, resourceProfile,
      correctionProvider, correctionModel, translationProvider, translationModel,
      title, presenter, preset, hotwords, referenceDocuments, displaySettings,
    }, { asr: asrRegistry, translation: translationRegistry });
    if (nextSession?.context?.reference_documents) {
      setReferenceDocuments(nextSession.context.reference_documents);
    }
  }

  if (workspacePage === "glossary") {
    return (
      <main className="app-shell glossary-workspace-page" data-testid="glossary-workspace">
        <header className="topbar">
          <div>
            <div className="brand">LangTextFlow</div>
            <div className="subtitle">{glossaryCopy.pageTitle}</div>
          </div>
          <nav className="product-nav" aria-label="Workspace">
            <button type="button" onClick={() => switchWorkspace("home")}>
              {glossaryCopy.backToLive}
            </button>
          </nav>
          <LanguageSwitcher compact />
        </header>
        <section className="panel glossary-workspace-panel">
          <h1>{glossaryCopy.pageTitle}</h1>
          <p>{glossaryCopy.pageHelp}</p>
          <label>
            {glossaryCopy.projectScope}
            <select
              data-testid="glossary-project-scope"
              value={glossaryProjectId}
              disabled={running}
              onChange={(event) => setGlossaryProjectId(event.target.value)}
            >
              <option value="global">{glossaryCopy.globalTerms}</option>
              {projects.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.title}
                </option>
              ))}
            </select>
          </label>
          <GlossaryManager
            apiUrl={API_URL}
            preset={preset}
            sourceLanguage={sourceLanguage}
            targetLanguage={glossaryTargetLanguage}
            projectId={glossaryProjectId === "global" ? null : glossaryProjectId}
            disabled={running}
          />
          <GlossaryRecommendations preset={preset} sourceLanguage={sourceLanguage} disabled={running} />
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <OnboardingWizard
        open={onboardingOpen && !running}
        engine={engine}
        resourceProfile={resourceProfile}
        translationProvider={translationProvider}
        translationModel={translationModel}
        sourceLanguage={sourceLanguage}
        targetLanguages={targetLanguages}
        preset={preset}
        onEngineChange={changeEngine}
        onTranslationProviderChange={setTranslationProvider}
        translationRegistry={translationRegistry}
        asrRegistry={asrRegistry}
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
          <button type="button" data-testid="glossary-nav" onClick={() => switchWorkspace("glossary")}>{glossaryCopy.managePage}</button>
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
            {operatorCopy.projectLabel}
            <select
              data-testid="session-project-select"
              value={projects.some((project) => project.project_id === selectedProjectId)
                ? selectedProjectId : ""}
              disabled={running}
              onChange={(event) => setSelectedProjectId(event.target.value || "all")}
            >
              <option value="">{operatorCopy.projectNone}</option>
              {projects.map((project) => (
                <option value={project.project_id} key={project.project_id}>{project.title}</option>
              ))}
            </select>
            {projectLoadError && <small role="alert">{projectLoadError}</small>}
          </label>
          <label className="recording-opt-in">
            <input
              type="checkbox"
              checked={recordAudio && needsAudio}
              disabled={running || !needsAudio}
              onChange={(event) => setRecordAudio(event.target.checked)}
            />
            <span>
              {recordingCopy.label}
            </span>
          </label>
          <small>
            {recordingCopy.help}
          </small>
          {session?.recording_error && <small role="alert">{session.recording_error}</small>}
          <label className="recording-opt-in">
            <input type="checkbox" checked={running ? Boolean(session?.collect_audience_metrics) : collectAudienceMetrics} disabled={running}
              data-testid="operator-metrics-consent"
              onChange={(event) => setCollectAudienceMetrics(event.target.checked)} />
            <span>{AUDIENCE_METRICS_COPY[locale].operatorConsent}</span>
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
            {operatorCopy.resourceLabel}
            <select
              data-testid="resource-profile-select"
              value={resourceProfile}
              onChange={(event) => changeResourceProfile(
                event.target.value as "standard" | "low",
              )}
              disabled={running}
            >
              <option value="standard">{operatorCopy.resourceStandard}</option>
              <option value="low">{operatorCopy.resourceLow}</option>
            </select>
            <small>{operatorCopy.resourceHelp}</small>
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
              onChange={(event) => setHotwordsByLanguage((values) => ({
                ...values, [sourceLanguage]: event.target.value,
              }))}
              disabled={running}
            />
            <small>{t("session.hotwordsHelp")}</small>
          </label>

          <ContextDocumentManager
            value={referenceDocuments}
            disabled={running}
            onChange={setReferenceDocuments}
          />

          <button
            type="button"
            className="secondary-button glossary-page-link"
            onClick={() => switchWorkspace("glossary")}
            data-testid="open-glossary-page"
          >
            {glossaryCopy.managePage}
          </button>
            </div>
          </details>

          <details className="settings-group">
            <summary>
              <span><strong>{operatorCopy.advancedGroup}</strong><small>{operatorCopy.advancedGroupHelp}</small></span>
            </summary>
            <div className="settings-group-body">
          <label>
            {t("engines.asr")}
            <AsrProviderSelect
              value={engine}
              onChange={changeEngine}
              disabled={running}
              sourceLanguage={sourceLanguage}
              registry={asrRegistry}
            />
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
                placeholder={operatorCopy.configuredDefaultModel}
                onChange={(event) => setCorrectionModel(event.target.value)}
                disabled={running}
              />
              <small>{operatorCopy.correctionDefaultHelp}</small>
            </label>
          )}
          <label>
            {t("engines.translation")}
            <TranslationProviderSelect
              value={translationProvider}
              onChange={setTranslationProvider}
              disabled={running}
              sourceLanguage={sourceLanguage}
              targetLanguages={targetLanguages}
              engine={engine}
              registry={translationRegistry}
            />
          </label>
          {translationProviderUsesModel(translationProvider) && (
            <label>
              {translationProvider === "ollama" ? t("engines.translationModel") : t("engines.apiModel")}
              <input
                value={translationModel}
                data-testid="translation-model-input"
                placeholder={translationRegistry.status === "ready"
                  ? translationRegistry.providers.find(provider => provider.provider === translationProvider)?.model_id || operatorCopy.configuredDefaultModel
                  : operatorCopy.configuredDefaultModel}
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
          {session?.recording_enabled && (
            <div className="warning-box" role="status">
              {session.recording_error
                ? `${recordingCopy.failed}: ${session.recording_error}`
                : running
                  ? recordingCopy.active
                  : recordingCopy.ended}
            </div>
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
            projects={projects}
            selectedProjectId={selectedProjectId}
            onSelectProject={setSelectedProjectId}
            onProjectsChanged={refreshProjects}
          />
          </div>
        </section>
      </div>

      {!running && (
        <PreflightPanel
          engine={engine}
          resourceProfile={resourceProfile}
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
