import { useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { asrBlockReason, type AsrRegistry } from "./asrCapabilities";
import { AudioCaptureController, requestAudioInputs, type AudioInputDevice } from "./audioCapture";
import { useI18n } from "./i18n";
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
export function useOperatorSession() {
  const { t } = useI18n();
  const captureRef = useRef<AudioCaptureController | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  // Consent belongs to one session and is never persisted in browser storage.
  const [recordAudio, setRecordAudio] = useState(false);
  const [collectAudienceMetrics, setCollectAudienceMetrics] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = session?.running ?? false;
  useEffect(() => { if (!running) setCollectAudienceMetrics(false); }, [running]);
  useEffect(() => () => {
    const capture = captureRef.current;
    captureRef.current = null;
    void capture?.stop().catch(() => undefined);
  }, []);

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
        const next = (await response.json()) as SessionState;
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

  async function start(settings: OperatorSessionSettings, providers: OperatorProviders) {
    const needsAudio = settings.engine !== "mock";
    setBusy(true);
    setError("");
    let nextSession: SessionState | null = null;
    let startConfirmed = false;
    try {
      await assertPreflightReady(settings, providers);

      let selectedDevice = deviceId;
      if (needsAudio && !selectedDevice) {
        const result = await requestAudioInputs();
        setDevices(result);
        selectedDevice = result[0]?.deviceId || "";
        setDeviceId(selectedDevice);
        if (!selectedDevice) throw new Error(t("audio.noDevice"));
      }

      // An HTTP validation error is not evidence this tab owns the current
      // server session. Roll back only after an acknowledged successful start.
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
      if (!response.ok) throw new Error(await response.text());
      startConfirmed = true;
      nextSession = (await response.json()) as SessionState;
      setSession(nextSession);
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
              setRecordAudio(false);
              setCollectAudienceMetrics(false);
            }
          })();
        });
        captureRef.current = capture;
        await capture.start(selectedDevice);
      }
    } catch (reason) {
      await rollbackFailedSessionStart(startConfirmed, {
        stopCapture: async () => {
          const capture = captureRef.current;
          captureRef.current = null;
          await capture?.stop();
        },
        stopRemote: async () => {
          await fetch(`${API_URL}/api/v1/session/stop`, { method: "POST" });
        },
      });
      if (startConfirmed) {
        setSession(null);
        setRecordAudio(false);
        setCollectAudienceMetrics(false);
      }
      nextSession = null;
      setError(reason instanceof Error ? reason.message : t("error.startFailed"));
    } finally {
      setBusy(false);
    }
    return nextSession;
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
      // Explicit consent is scoped to one session; never carry it to the next event.
      setRecordAudio(false);
      setCollectAudienceMetrics(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("error.stopFailed"));
    } finally {
      setBusy(false);
    }
  }

  return {
    session, running, busy, error, devices, deviceId, setDeviceId,
    recordAudio, setRecordAudio, collectAudienceMetrics, setCollectAudienceMetrics,
    refreshDevices, start, stop,
  };
}
