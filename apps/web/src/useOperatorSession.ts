import { useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { asrBlockReason, type AsrRegistry } from "./asrCapabilities";
import { AudioCaptureController, requestAudioInputs, type AudioInputDevice } from "./audioCapture";
import { useI18n } from "./i18n";
import { parseOperatorSessionState } from "./operatorSessionState";
import { buildSessionStartPayload, translationProviderUsesModel, type SessionStartInputs } from "./sessionStartPayload";
import { rollbackFailedSessionStart } from "./sessionStartRollback";
import { providerBlockReason, type TranslationRegistry } from "./translationCapabilities";
import type { SessionState, SystemPreflight } from "./types";

type OperatorSessionSettings = Omit<SessionStartInputs,
  "recordAudio" | "collectAudienceMetrics" | "needsAudio">;
interface OperatorProviders {
  asr: AsrRegistry;
  translation: TranslationRegistry;
}

/** Own only the live session and its microphone/consent lifecycle, not setup panels. */
export function useOperatorSession(
  connected = true, onHydrated?: (session: SessionState) => void,
) {
  const { t } = useI18n();
  const mountedRef = useRef(false);
  const captureRef = useRef<AudioCaptureController | null>(null);
  const sessionRef = useRef<SessionState | null>(null);
  const ownedSessionRef = useRef<string | null>(null);
  const actionEpoch = useRef(0);
  const hydrationEpoch = useRef(0);
  const actionBusy = useRef(false);
  const pendingHydration = useRef(false);
  const hydrateFormRef = useRef(onHydrated);
  hydrateFormRef.current = onHydrated;
  const [session, setSession] = useState<SessionState | null>(null);
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  // Consent belongs to one session and is never persisted in browser storage.
  const [recordAudio, setRecordAudio] = useState(false);
  const [collectAudienceMetrics, setCollectAudienceMetrics] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [recoveredAudio, setRecoveredAudio] = useState(false);
  const running = session?.running ?? false;
  useEffect(() => { if (!running) setCollectAudienceMetrics(false); }, [running]);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      actionEpoch.current += 1;
      hydrationEpoch.current += 1;
      const capture = captureRef.current;
      captureRef.current = null;
      void capture?.stop().catch(() => undefined);
    };
  }, []);

  function applySession(next: SessionState | null) {
    sessionRef.current = next;
    setSession(next);
    if (!next?.running) setRecoveredAudio(false);
  }

  async function synchronizeSession() {
    if (actionBusy.current) { pendingHydration.current = true; return; }
    const request = ++hydrationEpoch.current;
    const action = actionEpoch.current;
    const current = () => mountedRef.current && request === hydrationEpoch.current
      && action === actionEpoch.current && !actionBusy.current;
    try {
      const response = await fetch(`${API_URL}/api/v1/state`);
      if (!response.ok || !current()) return;
      const next = parseOperatorSessionState(await response.json());
      if (!current()) return;
      const previous = sessionRef.current;
      if (!next.running || next.session_id !== previous?.session_id) {
        const capture = captureRef.current;
        captureRef.current = null;
        await capture?.stop().catch(() => undefined);
        if (!current()) return;
      }
      // Reconnect to the same session preserves this tab's healthy microphone.
      // Recovery from another tab/reload never starts capture or replays consent.
      if (!next.running || ownedSessionRef.current !== next.session_id) {
        if (next.running || previous?.running) {
          setRecordAudio(false);
          setCollectAudienceMetrics(false);
        }
        ownedSessionRef.current = null;
      }
      applySession(next);
      setRecoveredAudio(next.running && next.audio_required
        && ownedSessionRef.current !== next.session_id);
      if (next.running) hydrateFormRef.current?.(next);
    } catch {
      // Invalid/temporary state responses never authorize capture or a remote stop.
    }
  }

  useEffect(() => {
    void synchronizeSession();
    return () => { hydrationEpoch.current += 1; };
  }, [connected]);

  // The recorder may fail while ASR continues; surface its health promptly.
  // Poll only the local operator state and only for opted-in live sessions.
  useEffect(() => {
    const sessionId = session?.session_id;
    if (!running || !session?.recording_enabled || !sessionId) return;
    let closed = false;
    const refreshRecordingHealth = async () => {
      try {
        const response = await fetch(`${API_URL}/api/v1/state`);
        if (!response.ok) return;
        const next = parseOperatorSessionState(await response.json());
        if (closed || next.session_id !== sessionId) return;
        setSession((current) => {
          if (!current?.running || current.session_id !== sessionId) return current;
          if (current.recording_error === next.recording_error) return current;
          return { ...current, recording_error: next.recording_error };
        });
      } catch {
        // A transient status poll failure must not affect live audio processing.
      }
    };
    const timer = window.setInterval(() => void refreshRecordingHealth(), 2500);
    return () => { closed = true; window.clearInterval(timer); };
  }, [running, session?.recording_enabled, session?.session_id]);

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

  async function assertPreflightReady(settings: OperatorSessionSettings, providers: OperatorProviders) {
    const { engine, sourceLanguage, targetLanguages, translationProvider,
      translationModel, resourceProfile } = settings;
    const { asr: asrRegistry, translation: translationRegistry } = providers;
    const asrReason = asrBlockReason(asrRegistry, engine, sourceLanguage);
    if (asrReason !== null) {
      throw new Error(`ASR provider unavailable or unsupported (${asrReason})`);
    }
    const reason = providerBlockReason(
      translationRegistry, translationProvider, sourceLanguage, targetLanguages, engine,
    );
    if (reason !== null) {
      throw new Error(`Translation provider unavailable or unsupported (${reason}); check provider configuration and languages`);
    }
    const query = new URLSearchParams({
      engine,
      translation_provider: translationProvider,
      resource_profile: resourceProfile,
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

  function requestStop(expectedSessionId: string) {
    return fetch(`${API_URL}/api/v1/session/stop`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_session_id: expectedSessionId }),
    });
  }

  async function start(settings: OperatorSessionSettings, providers: OperatorProviders) {
    if (!mountedRef.current || actionBusy.current) return null;
    const action = ++actionEpoch.current;
    actionBusy.current = true;
    const current = () => mountedRef.current && actionEpoch.current === action;
    const needsAudio = settings.engine !== "mock";
    setBusy(true);
    setError("");
    let nextSession: SessionState | null = null;
    let startedSessionId: string | null = null;
    let startConfirmed = false;
    try {
      await assertPreflightReady(settings, providers);
      if (!current()) return null;

      let selectedDevice = deviceId;
      if (needsAudio && !selectedDevice) {
        const result = await requestAudioInputs();
        if (!current()) return null;
        setDevices(result);
        selectedDevice = result[0]?.deviceId || "";
        setDeviceId(selectedDevice);
        if (!selectedDevice) throw new Error(t("audio.noDevice"));
      }

      // An HTTP validation error is not evidence this tab owns the current
      // server session. Remote rollback also requires the acknowledged session ID.
      const response = await fetch(`${API_URL}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildSessionStartPayload({
          ...settings,
          recordAudio,
          collectAudienceMetrics,
          needsAudio,
        })),
      });
      if (!current()) return null;
      if (!response.ok) throw new Error(await response.text());
      startConfirmed = true;
      nextSession = parseOperatorSessionState(await response.json());
      if (!current()) return null;
      startedSessionId = nextSession.session_id;
      applySession(nextSession);
      ownedSessionRef.current = nextSession.session_id;
      setRecoveredAudio(false);
      if (nextSession.audio_required) {
        const expectedSessionId = nextSession.session_id;
        const capture = new AudioCaptureController((message) => {
          const ownsCapture = () => current() && expectedSessionId !== null
            && ownedSessionRef.current === expectedSessionId
            && sessionRef.current?.session_id === expectedSessionId
            && captureRef.current === capture;
          const sameSession = () => current()
            && sessionRef.current?.session_id === expectedSessionId;
          if (!ownsCapture()) {
            void capture.stop().catch(() => undefined);
            return;
          }
          setError(message);
          void (async () => {
            await capture.stop().catch(() => undefined);
            // Hydration can replace ownership while local device shutdown is suspended.
            if (!ownsCapture() || !expectedSessionId) return;
            captureRef.current = null;
            ownedSessionRef.current = null;
            const stopResponse = await requestStop(expectedSessionId).catch(() => null);
            if (!sameSession()) return;
            if (stopResponse?.ok) {
              const stopped = parseOperatorSessionState(await stopResponse.json());
              if (!sameSession()) return;
              applySession(stopped);
              ownedSessionRef.current = null;
              setRecordAudio(false);
              setCollectAudienceMetrics(false);
            } else {
              void synchronizeSession();
            }
          })().catch(() => undefined);
        });
        captureRef.current = capture;
        await capture.start(selectedDevice);
        if (!current()) {
          await capture.stop().catch(() => undefined);
          return null;
        }
      }
    } catch (reason) {
      if (!current()) return null;
      await rollbackFailedSessionStart(startConfirmed, {
        stopCapture: async () => {
          const capture = captureRef.current;
          captureRef.current = null;
          await capture?.stop();
        },
        stopRemote: async () => {
          if (!current() || !startedSessionId
            || ownedSessionRef.current !== startedSessionId
            || sessionRef.current?.session_id !== startedSessionId) return;
          await requestStop(startedSessionId);
        },
      });
      if (!current()) return null;
      if (startConfirmed) {
        applySession(null);
        ownedSessionRef.current = null;
        setRecordAudio(false);
        setCollectAudienceMetrics(false);
      }
      nextSession = null;
      setError(reason instanceof Error ? reason.message : t("error.startFailed"));
      // A rejected start may mean another tab already owns a live session.
      pendingHydration.current = true;
    } finally {
      if (current()) {
        actionBusy.current = false;
        setBusy(false);
        if (pendingHydration.current) {
          pendingHydration.current = false;
          void synchronizeSession();
        }
      }
    }
    return nextSession;
  }

  async function stop() {
    if (!mountedRef.current) return;
    const action = ++actionEpoch.current;
    actionBusy.current = true;
    const current = () => mountedRef.current && actionEpoch.current === action;
    const expectedSessionId = sessionRef.current?.session_id;
    setBusy(true);
    setError("");
    try {
      const capture = captureRef.current;
      captureRef.current = null;
      ownedSessionRef.current = null;
      await capture?.stop();
      if (!current()) return;
      if (!expectedSessionId) {
        pendingHydration.current = true;
        return;
      }
      const response = await requestStop(expectedSessionId);
      if (!response.ok) throw new Error(await response.text());
      const stopped = parseOperatorSessionState(await response.json());
      if (!current()) return;
      applySession(stopped);
      ownedSessionRef.current = null;
      setRecoveredAudio(false);
      // Explicit consent is scoped to one session; never carry it to the next event.
      setRecordAudio(false);
      setCollectAudienceMetrics(false);
    } catch (reason) {
      if (!current()) return;
      setError(reason instanceof Error ? reason.message : t("error.stopFailed"));
      pendingHydration.current = true;
    } finally {
      if (current()) {
        actionBusy.current = false;
        setBusy(false);
        if (pendingHydration.current) {
          pendingHydration.current = false;
          void synchronizeSession();
        }
      }
    }
  }

  return {
    session, running, busy, error, recoveredAudio, devices, deviceId, setDeviceId,
    recordAudio, setRecordAudio, collectAudienceMetrics, setCollectAudienceMetrics,
    refreshDevices, start, stop,
  };
}
