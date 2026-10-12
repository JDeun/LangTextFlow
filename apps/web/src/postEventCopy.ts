import type { Locale } from "./locales";

export const POST_EVENT_COPY: Record<Locale, {
  heading: string; detail: string; upload: string; importLabel: string;
  summary: string; generate: string; refresh: string; generating: string;
  noSummary: string; failure: string; imported: string; warning: string;
  recording: string; selected: string; stale: string;
}> = {
  en: {
    heading: "Event archive", detail: "Transcript, optional recording, and AI summary are linked to this session.",
    upload: "Import WAV recording", importLabel: "Import a permitted uncompressed 16-bit WAV (max 512 MiB), or review audio captured during this session with recording enabled.",
    summary: "AI event summary", generate: "Generate / retry with local Ollama", refresh: "Refresh archive",
    generating: "Generating from the final source transcript…", noSummary: "No summary generated.",
    failure: "Archive request failed.", imported: "Recording imported.", warning: "This session ended after an interruption. Verify the transcript before sharing the summary.",
    recording: "Recording", selected: "Choose WAV recording",
    stale: "The committed transcript changed since this summary. Regenerate and review it before sharing.",
  },
  ko: {
    heading: "행사 아카이브", detail: "원문 전사문·녹음·AI 요약을 동일한 세션에서 관리합니다.",
    upload: "WAV 녹음 파일 가져오기", importLabel: "사용 권한이 있는 16비트 비압축 WAV를 가져올 수 있습니다(최대 512MiB). 세션 시작 시 동의 후 자동 녹음한 파일도 이곳에 표시됩니다.",
    summary: "AI 행사 요약", generate: "로컬 Ollama로 요약 생성 / 재시도", refresh: "기록 새로고침",
    generating: "최종 원문 전사문으로 요약을 생성 중입니다…", noSummary: "생성된 요약이 없습니다.",
    failure: "아카이브 요청을 처리하지 못했습니다.", imported: "녹음 파일을 연결했습니다.", warning: "비정상 종료된 세션입니다. 요약 공유 전에 전사 누락을 확인하세요.",
    recording: "녹음 파일", selected: "WAV 녹음 파일 선택",
    stale: "요약 생성 후 확정 전사문이 변경되었습니다. 공유 전 재생성·검수하세요.",
  },
  ja: {
    heading: "イベントアーカイブ", detail: "原文・録音・AI要約をセッション単位で管理します。",
    upload: "WAV録音を取り込む", importLabel: "利用権限のある16-bit非圧縮WAV（最大512MiB）を追加できます。開始時に同意して録音したファイルもここに表示されます。",
    summary: "AIイベント要約", generate: "ローカルOllamaで要約を生成・再試行", refresh: "アーカイブ更新",
    generating: "確定した原文から要約を生成中…", noSummary: "要約は未生成です。",
    failure: "アーカイブ処理に失敗しました。", imported: "録音を保存しました。", warning: "中断したセッションです。共有前に原文の欠落を確認してください。",
    recording: "録音", selected: "WAV録音を選択",
    stale: "要約生成後に確定原文が変更されました。共有前に再生成・確認してください。",
  },
};

