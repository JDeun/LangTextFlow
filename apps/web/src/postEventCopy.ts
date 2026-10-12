import type { Locale } from "./locales";

export const POST_EVENT_COPY: Record<Locale, {
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

