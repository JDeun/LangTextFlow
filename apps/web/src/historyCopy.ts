import type { Locale } from "./locales";

interface HistoryCopy {
  loadFailed: string;
  detailFailed: string;
  titleRequired: string;
  saveFailed: string;
  deleteFailed: string;
  heading: string;
  headingHelp: string;
  refresh: string;
  searchPlaceholder: string;
  noMatch: string;
  noSessions: string;
  presenterMissing: string;
  recovered: string;
  reopen: string;
  details: string;
  delete: string;
  detail: string;
  running: string;
  recoveredShort: string;
  close: string;
  presenter: string;
  unspecified: string;
  translation: string;
  disabled: string;
  endStatus: string;
  autoRecovered: string;
  recordTitle: string;
  notes: string;
  notesPlaceholder: string;
  contextImmutable: string;
  activeEditHint: string;
  saving: string;
  saveMetadata: string;
  displayLanguage: string;
  source: string;
  transcriptSearch: string;
  transcriptPlaceholder: string;
  allJson: string;
  noCaptionMatch: string;
  noCaptions: string;
}

export const HISTORY_COPY: Record<Locale, HistoryCopy> = {
  en: {
    loadFailed: "Could not load session history.", detailFailed: "Could not load session details.",
    titleRequired: "Session title cannot be empty.", saveFailed: "Could not save session metadata.", deleteFailed: "Could not delete the session history.",
    heading: "Session history", headingHelp: "Open a session to organize its title and notes or search the full source/translated transcript.", refresh: "Refresh",
    searchPlaceholder: "Search title, notes, presenter, engine, or language", noMatch: "No sessions match the current search.", noSessions: "No saved sessions.",
    presenterMissing: "No presenter", recovered: "recovered after interruption", reopen: "Reopen", details: "Details / search", delete: "Delete", detail: "Session detail",
    running: "in progress", recoveredShort: "interruption recovered", close: "Close", presenter: "Presenter", unspecified: "Unspecified", translation: "Translation", disabled: "Off",
    endStatus: "End status", autoRecovered: "Automatically recovered after interruption", recordTitle: "Record title", notes: "Operator notes",
    notesPlaceholder: "Record follow-up work, quality issues, or event information.", contextImmutable: "Session Context used by the models during the live session is not edited.",
    activeEditHint: " The active session can be edited after it ends.", saving: "Saving…", saveMetadata: "Save title / notes", displayLanguage: "Display language",
    source: "Source", transcriptSearch: "Transcript search", transcriptPlaceholder: "Search source, all translations, and speaker", allJson: "Full JSON",
    noCaptionMatch: "No captions match the search.", noCaptions: "No saved captions.",
  },
  ko: {
    loadFailed: "세션 기록을 불러오지 못했습니다.", detailFailed: "세션 상세 기록을 불러오지 못했습니다.",
    titleRequired: "세션 제목은 비워둘 수 없습니다.", saveFailed: "세션 메타데이터를 저장하지 못했습니다.", deleteFailed: "세션 기록을 삭제하지 못했습니다.",
    heading: "세션 기록", headingHelp: "세션을 열어 제목·메모를 정리하고 전체 원문/번역 transcript를 검색할 수 있습니다.", refresh: "새로고침",
    searchPlaceholder: "세션 제목, 메모, 발표자, 엔진, 언어 검색", noMatch: "검색 조건과 일치하는 세션이 없습니다.", noSessions: "저장된 세션이 없습니다.",
    presenterMissing: "발표자 미지정", recovered: "비정상 종료 후 복구됨", reopen: "다시 열기", details: "상세 / 검색", delete: "삭제", detail: "세션 상세",
    running: "진행 중", recoveredShort: "비정상 종료 복구", close: "닫기", presenter: "발표자", unspecified: "미지정", translation: "번역", disabled: "사용 안 함",
    endStatus: "종료 상태", autoRecovered: "비정상 종료 후 자동 복구", recordTitle: "기록용 제목", notes: "운영 메모",
    notesPlaceholder: "후속 작업, 품질 이슈, 행사 정보 등을 기록하세요.", contextImmutable: "라이브 당시 모델 입력인 Session Context는 수정하지 않습니다.",
    activeEditHint: " 현재 진행 중인 세션은 종료 후 편집할 수 있습니다.", saving: "저장 중…", saveMetadata: "제목 / 메모 저장", displayLanguage: "표시 언어",
    source: "원문", transcriptSearch: "Transcript 검색", transcriptPlaceholder: "원문·모든 번역·speaker에서 검색", allJson: "전체 JSON",
    noCaptionMatch: "검색어와 일치하는 자막이 없습니다.", noCaptions: "저장된 자막이 없습니다.",
  },
  ja: {
    loadFailed: "セッション履歴を読み込めませんでした。", detailFailed: "セッション詳細を読み込めませんでした。",
    titleRequired: "セッション名は空にできません。", saveFailed: "セッション情報を保存できませんでした。", deleteFailed: "セッション履歴を削除できませんでした。",
    heading: "セッション履歴", headingHelp: "セッションを開いてタイトルやメモを整理し、原文と翻訳を含む全文トランスクリプトを検索できます。", refresh: "更新",
    searchPlaceholder: "タイトル、メモ、発表者、エンジン、言語を検索", noMatch: "検索条件に一致するセッションはありません。", noSessions: "保存済みセッションはありません。",
    presenterMissing: "発表者未指定", recovered: "異常終了後に復旧", reopen: "再度開く", details: "詳細 / 検索", delete: "削除", detail: "セッション詳細",
    running: "進行中", recoveredShort: "異常終了から復旧", close: "閉じる", presenter: "発表者", unspecified: "未指定", translation: "翻訳", disabled: "使用しない",
    endStatus: "終了状態", autoRecovered: "異常終了後に自動復旧", recordTitle: "記録用タイトル", notes: "運用メモ",
    notesPlaceholder: "フォローアップ、品質課題、イベント情報などを記録してください。", contextImmutable: "ライブ時にモデルへ渡した Session Context は変更しません。",
    activeEditHint: " 現在進行中のセッションは終了後に編集できます。", saving: "保存中…", saveMetadata: "タイトル / メモを保存", displayLanguage: "表示言語",
    source: "原文", transcriptSearch: "トランスクリプト検索", transcriptPlaceholder: "原文・全翻訳・speakerから検索", allJson: "全JSON",
    noCaptionMatch: "検索語に一致する字幕はありません。", noCaptions: "保存された字幕はありません。",
  },
};
