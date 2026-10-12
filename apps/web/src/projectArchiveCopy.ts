import type { Locale } from "./locales";

export const PROJECT_ARCHIVE_COPY: Record<Locale, {
  title: string; sub: string; loading: string; retry: string;
  none: string; transcript: string; recording: string; summary: string;
  stale: string; current: string; absent: string; running: string;
  page: string; previous: string; next: string; export: string;
  open: string; failure: string; interrupted: string; report: string;
}> = {
  ko: {
    title: "행사 아카이브 목록", sub: "세션별 녹음·요약 현황과 전사문 최신성을 확인합니다.",
    loading: "아카이브 불러오는 중", retry: "다시 불러오기",
    none: "이 행사에 연결된 세션이 없습니다.", transcript: "전사 구간", recording: "녹음",
    summary: "요약", stale: "전사 변경 — 요약 재검토 필요", current: "요약 생성 당시 전사에서 변경 없음 · 내용 검수 필요",
    absent: "없음", running: "생성 중", page: "세션",
    previous: "이전", next: "다음", export: "현재 페이지 JSON 저장",
    open: "세션 열기", failure: "아카이브 정보를 불러오지 못했습니다.",
    interrupted: "중단된 세션", report: "현재 페이지 근거 보고서(Markdown)",
  },
  en: {
    title: "Event archive index", sub: "Review recordings, summaries and transcript freshness for each session.",
    loading: "Loading event archive", retry: "Retry",
    none: "No sessions are linked to this event.", transcript: "Segments", recording: "Recordings",
    summary: "Summary", stale: "Transcript changed — review summary", current: "Source revision unchanged · summary not verified",
    absent: "None", running: "Generating", page: "Sessions",
    previous: "Previous", next: "Next", export: "Export current page as JSON",
    open: "Open session", failure: "Could not load event archive.",
    interrupted: "Interrupted session", report: "Download cited report (Markdown)",
  },
  ja: {
    title: "イベントアーカイブ一覧", sub: "セッションごとの録音・要約・原文の更新状態を確認します。",
    loading: "アーカイブを読み込み中", retry: "再読み込み",
    none: "このイベントにセッションはありません。", transcript: "文字起こし", recording: "録音",
    summary: "要約", stale: "原文変更 — 要約の再確認が必要", current: "原文の版は変更なし・内容確認が必要",
    absent: "なし", running: "生成中", page: "セッション",
    previous: "前へ", next: "次へ", export: "表示ページをJSON保存",
    open: "セッションを開く", failure: "アーカイブを読み込めませんでした。",
    interrupted: "中断セッション", report: "表示ページの根拠レポート(Markdown)",
  },
};

