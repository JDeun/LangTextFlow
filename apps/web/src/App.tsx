import { useMemo, useState } from "react";
import { useCaptionSocket } from "./useCaptionSocket";
import type { SessionState, TranscriptEvent } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const LANGUAGES = [
  ["ko", "한국어"],
  ["en", "English"],
  ["ja", "日本語"],
  ["zh", "中文"],
] as const;

function displayCaption(segment: TranscriptEvent | undefined, targetLanguage: string) {
  if (!segment) return "말하기를 시작하면 자막이 이곳에 표시됩니다.";
  return segment.translations[targetLanguage] || segment.text;
}

export default function App() {
  const { connected, segments } = useCaptionSocket();
  const [sourceLanguage, setSourceLanguage] = useState("ko");
  const [targetLanguage, setTargetLanguage] = useState("en");
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);

  const latest = segments.at(-1);
  const recent = useMemo(() => segments.slice(-6).reverse(), [segments]);

  async function start() {
    setBusy(true);
    try {
      const response = await fetch(`${API_URL}/api/v1/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_language: sourceLanguage,
          target_languages: [targetLanguage],
          engine: "mock",
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const state = (await response.json()) as SessionState;
      setRunning(state.running);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      const response = await fetch(`${API_URL}/api/v1/session/stop`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      setRunning(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <div className="brand">LangTextFlow</div>
          <div className="subtitle">Realtime multilingual captioning</div>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="dot" /> {connected ? "서버 연결됨" : "서버 연결 중"}
        </div>
      </header>

      <div className="workspace">
        <aside className="control-panel panel">
          <div className="section-heading">
            <span>세션 설정</span>
            <span className="beta">P0</span>
          </div>

          <label>
            입력 언어
            <select value={sourceLanguage} onChange={(e) => setSourceLanguage(e.target.value)} disabled={running}>
              {LANGUAGES.map(([code, label]) => <option value={code} key={code}>{label}</option>)}
            </select>
          </label>

          <label>
            자막 언어
            <select value={targetLanguage} onChange={(e) => setTargetLanguage(e.target.value)} disabled={running}>
              {LANGUAGES.map(([code, label]) => <option value={code} key={code}>{label}</option>)}
            </select>
          </label>

          <label>
            음성 인식 엔진
            <select value="mock" disabled>
              <option value="mock">Demo streaming engine</option>
            </select>
            <small>VibeVoice / faster-whisper는 다음 단계에서 연결합니다.</small>
          </label>

          <button className={running ? "stop-button" : "start-button"} onClick={running ? stop : start} disabled={busy || !connected}>
            {busy ? "처리 중…" : running ? "자막 중지" : "데모 시작"}
          </button>

          <div className="pipeline-card">
            <span>실시간 상태</span>
            <strong>{latest?.stage ?? "idle"}</strong>
            <div className="stage-track">
              {['partial', 'stable', 'corrected', 'translated', 'committed'].map((stage) => (
                <i key={stage} className={latest?.stage === stage ? "active" : ""} title={stage} />
              ))}
            </div>
          </div>
        </aside>

        <section className="main-column">
          <div className="preview panel">
            <div className="preview-toolbar">
              <span>Audience Preview</span>
              <span>{targetLanguage.toUpperCase()} · 16:9</span>
            </div>
            <div className="stage-screen">
              <div className={`caption ${latest?.stage === "partial" ? "draft" : ""}`}>
                {displayCaption(latest, targetLanguage)}
              </div>
              {latest && latest.translations[targetLanguage] && (
                <div className="source-caption">{latest.text}</div>
              )}
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
