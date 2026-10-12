import { useCallback, useEffect, useState } from "react";
import type { SessionDetail } from "./types";
import type { Locale } from "./locales";

interface Recording {
  recording_id: string;
  size_bytes: number;
  duration_seconds: number;
  sha256: string;
  created_at: string;
}

interface Summary {
  summary_id: string;
  language: string;
  provider: string;
  model: string;
  status: "running" | "completed" | "error";
  content: string;
  error: string | null;
  transcript_sha256: string;
  created_at: string;
}

interface Archive {
  recordings: Recording[];
  summary: Summary | null;
}

const COPY: Record<Locale, {
  heading: string; detail: string; upload: string; importLabel: string;
  summary: string; generate: string; refresh: string; generating: string;
  noSummary: string; failure: string; imported: string; warning: string;
  recording: string; selected: string;
}> = {
  en: {
    heading: "Event archive", detail: "Transcript, optional recording, and AI summary are linked to this session.",
    upload: "Import WAV recording", importLabel: "Import an authorized, uncompressed 16-bit WAV (max 512 MiB). Automatic recording is not enabled yet.",
    summary: "AI event summary", generate: "Generate / retry with local Ollama", refresh: "Refresh archive",
    generating: "Generating from the final source transcript…", noSummary: "No summary generated.",
    failure: "Archive request failed.", imported: "Recording imported.", warning: "This session ended after an interruption. Verify the transcript before sharing the summary.",
    recording: "Recording", selected: "Choose WAV recording",
  },
  ko: {
    heading: "행사 아카이브", detail: "원문 전사문·녹음·AI 요약을 동일한 세션에서 관리합니다.",
    upload: "WAV 녹음 파일 가져오기", importLabel: "사용 권한이 있는 16비트 비압축 WAV를 선택하세요(최대 512MiB). 자동 녹음은 아직 지원하지 않습니다.",
    summary: "AI 행사 요약", generate: "로컬 Ollama로 요약 생성 / 재시도", refresh: "기록 새로고침",
    generating: "최종 원문 전사문으로 요약을 생성 중입니다…", noSummary: "생성된 요약이 없습니다.",
    failure: "아카이브 요청을 처리하지 못했습니다.", imported: "녹음 파일을 연결했습니다.", warning: "비정상 종료된 세션입니다. 요약 공유 전에 전사 누락을 확인하세요.",
    recording: "녹음 파일", selected: "WAV 녹음 파일 선택",
  },
  ja: {
    heading: "イベントアーカイブ", detail: "原文・録音・AI要約をセッション単位で管理します。",
    upload: "WAV録音を取り込む", importLabel: "権利を持つ16-bit非圧縮WAV（最大512MiB）。自動録音はまだ未対応です。",
    summary: "AIイベント要約", generate: "ローカルOllamaで要約を生成・再試行", refresh: "アーカイブ更新",
    generating: "確定した原文から要約を生成中…", noSummary: "要約は未生成です。",
    failure: "アーカイブ処理に失敗しました。", imported: "録音を保存しました。", warning: "中断したセッションです。共有前に原文の欠落を確認してください。",
    recording: "録音", selected: "WAV録音を選択",
  },
};

interface Props {
  apiUrl: string;
  session: SessionDetail;
  locale: Locale;
}

export function PostEventPanel({ apiUrl, session, locale }: Props) {
  const copy = COPY[locale];
  const [archive, setArchive] = useState<Archive>({ recordings: [], summary: null });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const root = `${apiUrl}/api/v1/history/${encodeURIComponent(session.session_id)}`;

  const refresh = useCallback(async () => {
    const response = await fetch(`${root}/archive`);
    if (!response.ok) throw new Error(await response.text());
    const data = (await response.json()) as Archive;
    setArchive(data);
  }, [root]);

  useEffect(() => {
    let mounted = true;
    fetch(`${root}/archive`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json() as Promise<Archive>;
      })
      .then((data) => { if (mounted) setArchive(data); })
      .catch(() => { if (mounted) setError(copy.failure); });
    return () => { mounted = false; };
  }, [root, copy.failure]);

  useEffect(() => {
    if (archive.summary?.status !== "running") return;
    const timer = window.setInterval(() => {
      void refresh().catch(() => setError(copy.failure));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [archive.summary?.status, copy.failure, refresh]);

  async function upload() {
    if (!file || busy) return;
    setBusy("upload");
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${root}/recordings`, {
        method: "POST",
        headers: { "Content-Type": "audio/wav" },
        body: file,
      });
      if (!response.ok) throw new Error(await response.text());
      setFile(null);
      setMessage(copy.imported);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally {
      setBusy("");
    }
  }

  async function summarize() {
    if (busy || archive.summary?.status === "running") return;
    setBusy("summary");
    setError("");
    try {
      const response = await fetch(`${root}/summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: locale, provider: "ollama" }),
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.failure);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="history-post-event" aria-label={copy.heading}>
      <h3>{copy.heading}</h3>
      <p>{copy.detail}</p>
      {session.interrupted && <p role="status">{copy.warning}</p>}
      <div className="history-post-actions">
        <button className="secondary-button" type="button" onClick={() => void refresh().catch(() => setError(copy.failure))}>
          {copy.refresh}
        </button>
      </div>
      <h4>{copy.recording}</h4>
      <label className="history-post-upload">
        {copy.importLabel}
        <input
          type="file"
          accept=".wav,audio/wav,audio/x-wav"
          aria-label={copy.selected}
          onChange={(event) => setFile(event.target.files?.[0] || null)}
        />
      </label>
      <button
        className="secondary-button"
        type="button"
        disabled={!file || Boolean(busy)}
        onClick={() => void upload()}
      >
        {copy.upload}
      </button>
      {archive.recordings.map((recording) => (
        <div className="history-post-recording" key={recording.recording_id}>
          <small>{Math.round(recording.duration_seconds)}s · {(recording.size_bytes / (1024 * 1024)).toFixed(1)} MiB</small>
          <audio
            controls
            preload="none"
            src={`${root}/recordings/${encodeURIComponent(recording.recording_id)}/audio`}
            aria-label={copy.recording}
          />
        </div>
      ))}
      <h4>{copy.summary}</h4>
      <button
        type="button"
        className="secondary-button"
        disabled={Boolean(busy) || archive.summary?.status === "running"}
        onClick={() => void summarize()}
      >
        {copy.generate}
      </button>
      {archive.summary?.status === "running" && <p role="status">{copy.generating}</p>}
      {archive.summary?.status === "error" && <p role="alert">{archive.summary.error || copy.failure}</p>}
      {archive.summary?.status === "completed" && (
        <div className="history-post-summary">
          <small>{archive.summary.model} · {archive.summary.language.toUpperCase()}</small>
          <pre>{archive.summary.content}</pre>
        </div>
      )}
      {!archive.summary && <p>{copy.noSummary}</p>}
      {message && <p role="status">{message}</p>}
      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  );
}
