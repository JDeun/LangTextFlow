import { useEffect, useMemo, useState } from "react";
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
  const [language, setLanguage] = useState("");
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
  const [sourceLanguage, setSourceLanguage] = useState("ko");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [title, setTitle] = useState("새 실시간 자막 세션");
  const [presenter, setPresenter] = useState("");
  const [preset, setPreset] = useState<ProductPreset>("church");
  const [hotwords, setHotwords] = useState("요한복음, 로마서, 복음, 은혜, 칭의, 성화");
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

  async function start() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API_URL}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_language: sourceLanguage,
          target_languages: [targetLanguage],
          engine: "mock",
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
      setSession((await response.json()) as SessionState);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "세션을 시작하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setError("");
    try {
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
            <span className="beta">P1A</span>
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
            <small>쉼표 또는 줄바꿈으로 구분합니다. 향후 VibeVoice hotword와 교정/번역 용어집에 함께 사용됩니다.</small>
          </label>
          <label>
            음성 인식 엔진
            <select value="mock" disabled>
              <option value="mock">Demo streaming engine</option>
            </select>
            <small>실제 VibeVoice / faster-whisper 연결은 P1B에서 추가합니다.</small>
          </label>

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
