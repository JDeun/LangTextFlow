import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudioCaptureController,
  requestAudioInputs,
  type AudioInputDevice,
} from "./audioCapture";
import { GlossaryManager } from "./GlossaryManager";
import { useCaptionSocket } from "./useCaptionSocket";
import type {
  AudienceSessionView,
  ProductPreset,
  SessionState,
  TranscriptEvent,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const LANGUAGES = [
  ["ko", "한국어"],
  ["en", "English"],
  ["ja", "日本語"],
  ["zh", "中文"],
] as const;

const PRESETS: Array<[ProductPreset, string]> = [
  ["general", "일반"],
  ["church", "교회 / 선교 집회"],
  ["conference", "컨퍼런스"],
  ["lecture", "강의"],
];

function displayCaption(segment: TranscriptEvent | undefined, targetLanguage: string) {
  if (!segment) return "말하기를 시작하면 자막이 이곳에 표시됩니다.";
  return segment.translations[targetLanguage] || segment.text;
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

function AudienceApp({ joinCode, displayMode }: { joinCode: string; displayMode: boolean }) {
  const { connected, segments } = useCaptionSocket(`/ws/audience/${encodeURIComponent(joinCode)}`);
  const [session, setSession] = useState<AudienceSessionView | null>(null);
  const [error, setError] = useState("");
  const queryLanguage = new URLSearchParams(window.location.search).get("lang") ?? "";
  const [language, setLanguage] = useState(queryLanguage);
  const latest = segments.at(-1);

  useEffect(() => {
    fetch(`${API_URL}/api/v1/audience/${encodeURIComponent(joinCode)}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("유효하지 않거나 종료된 세션입니다.");
        return (await response.json()) as AudienceSessionView;
      })
      .then((data) => {
        setSession(data);
        setLanguage((current) => current || data.target_languages[0] || data.source_language);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [joinCode]);

  if (error) return <main className="audience-error">{error}</main>;
  if (!session || !language) return <main className="audience-error">세션을 불러오는 중입니다…</main>;

  if (displayMode) {
    const mode = new URLSearchParams(window.location.search).get("mode") ?? "projector";
    return (
      <main className={`display-shell ${mode}`}>
        <div className={`display-caption ${latest?.stage === "partial" ? "draft" : ""}`}>
          {displayCaption(latest, language)}
        </div>
        {latest?.translations[language] && <div className="display-source">{latest.text}</div>}
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
          <span className="dot" /> {connected ? "LIVE" : "연결 중"}
        </div>
      </header>
      <label className="audience-language">
        자막 언어
        <select value={language} onChange={(event) => setLanguage(event.target.value)}>
          {session.target_languages.map((code) => (
            <option key={code} value={code}>{code.toUpperCase()}</option>
          ))}
        </select>
      </label>
      <section className="audience-caption-card">
        <div className={`audience-caption ${latest?.stage === "partial" ? "draft" : ""}`}>
          {displayCaption(latest, language)}
        </div>
        {latest?.translations[language] && <div className="audience-source">{latest.text}</div>}
      </section>
      <section className="audience-transcript">
        {segments.slice(-8).reverse().map((segment) => (
          <article key={segment.segment_id}>
            <span>{displayCaption(segment, language)}</span>
            {segment.stage === "partial" && <small>draft</small>}
          </article>
        ))}
      </section>
    </main>
  );
}

function OperatorApp() {
  const { connected, segments } = useCaptionSocket();
  const captureRef = useRef<AudioCaptureController | null>(null);
  const [sourceLanguage, setSourceLanguage] = useState("ko");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [title, setTitle] = useState("새 실시간 자막 세션");
  const [presenter, setPresenter] = useState("");
  const [preset, setPreset] = useState<ProductPreset>("church");
  const [hotwords, setHotwords] = useState("요한복음, 로마서, 복음, 은혜, 칭의, 성화");
  const [engine, setEngine] = useState("mock");
  const [translationProvider, setTranslationProvider] = useState("demo");
  const [translationModel, setTranslationModel] = useState("translategemma:4b");
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [session, setSession] = useState<SessionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const running = session?.running ?? false;
  const latest = segments.at(-1);
  const recent = useMemo(() => segments.slice(-6).reverse(), [segments]);
  const joinUrl = session?.join_code ? `${window.location.origin}/audience/${session.join_code}` : "";
  const projectorUrl = session?.join_code
    ? `${window.location.origin}/display/${session.join_code}?mode=projector&lang=${targetLanguage}`
    : "";
  const obsUrl = session?.join_code
    ? `${window.location.origin}/display/${session.join_code}?mode=obs&lang=${targetLanguage}`
    : "";

  function changeEngine(nextEngine: string) {
    setEngine(nextEngine);
    setTranslationProvider(nextEngine === "mock" ? "demo" : "ollama");
  }

  async function refreshDevices() {
    setError("");
    try {
      const result = await requestAudioInputs();
      setDevices(result);
      setDeviceId((current) => current || result[0]?.deviceId || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "오디오 장치를 찾지 못했습니다.");
    }
  }

  async function start() {
    setBusy(true);
    setError("");
    try {
      let selectedDevice = deviceId;
      if (engine === "vibevoice" && !selectedDevice) {
        const result = await requestAudioInputs();
        setDevices(result);
        selectedDevice = result[0]?.deviceId || "";
        setDeviceId(selectedDevice);
        if (!selectedDevice) throw new Error("사용 가능한 오디오 입력 장치가 없습니다.");
      }

      const response = await fetch(`${API_URL}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_language: sourceLanguage,
          target_languages: [targetLanguage],
          engine,
          translation_provider: translationProvider,
          translation_model: translationProvider === "ollama" ? translationModel : null,
          context: {
            title,
            presenter: presenter || null,
            preset,
            description: "",
            hotwords: splitHotwords(hotwords),
            glossary: [],
            output_modes: ["audience", "projector", "obs"],
            audience_access: true,
          },
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const nextSession = (await response.json()) as SessionState;
      setSession(nextSession);

      if (nextSession.audio_required) {
        const capture = new AudioCaptureController();
        captureRef.current = capture;
        await capture.start(selectedDevice);
      }
    } catch (reason) {
      await captureRef.current?.stop();
      captureRef.current = null;
      await fetch(`${API_URL}/api/v1/session/stop`, { method: "POST" }).catch(() => undefined);
      setSession(null);
      setError(reason instanceof Error ? reason.message : "세션을 시작하지 못했습니다.");
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
      setError(reason instanceof Error ? reason.message : "세션을 중지하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="brand">LangTextFlow</div>
          <div className="subtitle">Local-first realtime multilingual captioning</div>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="dot" /> {connected ? "서버 연결됨" : "서버 연결 중"}
        </div>
      </header>

      <div className="workspace">
        <aside className="control-panel panel">
          <div className="section-heading">
            <span>세션 설정</span>
            <span className="beta">P2B</span>
          </div>

          <label>
            세션 이름
            <input value={title} onChange={(event) => setTitle(event.target.value)} disabled={running} />
          </label>
          <label>
            발표자 / 강사
            <input value={presenter} onChange={(event) => setPresenter(event.target.value)} disabled={running} placeholder="선택 사항" />
          </label>
          <label>
            사용 목적
            <select value={preset} onChange={(event) => setPreset(event.target.value as ProductPreset)} disabled={running}>
              {PRESETS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <div className="language-grid">
            <label>
              입력 언어
              <select value={sourceLanguage} onChange={(event) => setSourceLanguage(event.target.value)} disabled={running}>
                {LANGUAGES.map(([code, label]) => <option value={code} key={code}>{label}</option>)}
              </select>
            </label>
            <label>
              자막 언어
              <select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)} disabled={running}>
                {LANGUAGES.map(([code, label]) => <option value={code} key={code}>{label}</option>)}
              </select>
            </label>
          </div>
          <label>
            중요 용어 / Hotwords
            <textarea rows={4} value={hotwords} onChange={(event) => setHotwords(event.target.value)} disabled={running} />
            <small>일회성 세션 힌트입니다. 반복 사용할 용어는 아래 용어집에 저장하세요.</small>
          </label>

          <GlossaryManager
            apiUrl={API_URL}
            preset={preset}
            targetLanguage={targetLanguage}
            disabled={running}
          />

          <label>
            음성 인식 엔진
            <select value={engine} onChange={(event) => changeEngine(event.target.value)} disabled={running}>
              <option value="mock">Demo engine</option>
              <option value="vibevoice">VibeVoice Streaming (local sidecar)</option>
            </select>
          </label>
          <label>
            번역 엔진
            <select value={translationProvider} onChange={(event) => setTranslationProvider(event.target.value)} disabled={running}>
              {engine === "mock" && <option value="demo">Demo translator</option>}
              <option value="ollama">Ollama (local)</option>
              <option value="none">번역 사용 안 함</option>
            </select>
          </label>
          {translationProvider === "ollama" && (
            <label>
              Ollama 번역 모델
              <input value={translationModel} onChange={(event) => setTranslationModel(event.target.value)} disabled={running} />
              <small>권장 시작점: translategemma:4b</small>
            </label>
          )}

          {engine === "vibevoice" && (
            <div className="device-block">
              <label>
                오디오 입력
                <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)} disabled={running}>
                  <option value="">{devices.length ? "기본 입력 장치" : "장치를 먼저 찾으세요"}</option>
                  {devices.map((device) => <option value={device.deviceId} key={device.deviceId}>{device.label}</option>)}
                </select>
              </label>
              <button className="secondary-button device-button" onClick={refreshDevices} disabled={running || busy}>
                마이크 권한 / 장치 새로고침
              </button>
            </div>
          )}

          {error && <div className="error-box">{error}</div>}
          <button className={running ? "stop-button" : "start-button"} onClick={running ? stop : start} disabled={busy || !connected}>
            {busy ? "처리 중…" : running ? "자막 중지" : "세션 시작"}
          </button>

          <div className="pipeline-card">
            <span>실시간 상태</span>
            <strong>{latest?.stage ?? "idle"}</strong>
            <div className="stage-track">
              {["partial", "stable", "corrected", "translated", "committed"].map((stage) => (
                <i key={stage} className={latest?.stage === stage ? "active" : ""} title={stage} />
              ))}
            </div>
            {session?.audio_sample_rate && <small>{session.engine} · {session.audio_sample_rate} Hz</small>}
            {session?.translation_status.enabled && (
              <small>
                번역: {session.translation_status.provider} / {session.translation_status.model || "default"}
                {session.translation_status.available ? " · ready" : ` · unavailable: ${session.translation_status.error || "unknown"}`}
              </small>
            )}
          </div>
        </aside>

        <section className="main-column">
          {session?.join_code && (
            <div className="share-panel panel">
              <div>
                <span className="eyebrow">Audience access</span>
                <strong className="join-code">{session.join_code}</strong>
                <span className="share-note">같은 네트워크에서 청중용 페이지를 열 수 있습니다.</span>
              </div>
              <div className="share-links">
                <button className="secondary-button" onClick={() => copy(joinUrl)}>청중 링크 복사</button>
                <button className="secondary-button" onClick={() => window.open(projectorUrl, "_blank")}>프로젝터 열기</button>
                <button className="secondary-button" onClick={() => copy(obsUrl)}>OBS URL 복사</button>
              </div>
            </div>
          )}

          <div className="preview panel">
            <div className="preview-toolbar">
              <span>Audience Preview</span>
              <span>{targetLanguage.toUpperCase()} · 16:9</span>
            </div>
            <div className="stage-screen">
              <div className={`caption ${latest?.stage === "partial" ? "draft" : ""}`}>
                {displayCaption(latest, targetLanguage)}
              </div>
              {latest?.translations[targetLanguage] && <div className="source-caption">{latest.text}</div>}
            </div>
          </div>

          <div className="transcript panel">
            <div className="section-heading">실시간 세그먼트</div>
            <div className="transcript-list">
              {recent.length === 0 && <div className="empty">아직 수신된 세그먼트가 없습니다.</div>}
              {recent.map((segment) => (
                <article className="segment" key={segment.segment_id}>
                  <div className="segment-meta">
                    <span>{segment.segment_id}</span>
                    <span className={`stage-tag ${segment.stage}`}>{segment.stage}</span>
                    <span>v{segment.version}</span>
                  </div>
                  <div className="segment-text">{displayCaption(segment, targetLanguage)}</div>
                  {segment.translations[targetLanguage] && <div className="segment-source">{segment.text}</div>}
                </article>
              ))}
            </div>
          </div>
        </section>
      </div>
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
