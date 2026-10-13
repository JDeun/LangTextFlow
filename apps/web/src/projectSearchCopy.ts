import type { Locale } from "./locales";

export const PROJECT_SEARCH_COPY: Record<Locale, {
  heading: string; helper: string; placeholder: string; search: string;
  more: string; noResults: string; error: string; showSession: string;
  original: string;
}> = {
  ko: {
    heading: "행사 전체 전사 검색",
    helper: "이 행사에 연결된 세션의 확정 원문·번역문에서 검색합니다.",
    placeholder: "키워드 또는 발언 내용",
    search: "검색", more: "결과 더 보기", noResults: "검색 결과가 없습니다.",
    error: "전사 검색에 실패했습니다.", showSession: "세션에서 확인",
    original: "원문",
  },
  en: {
    heading: "Search event transcripts",
    helper: "Find committed source and translated captions across this event's sessions.",
    placeholder: "Keyword or phrase",
    search: "Search", more: "More results", noResults: "No matching transcript segments.",
    error: "Transcript search failed.", showSession: "Open source session",
    original: "Source",
  },
  ja: {
    heading: "イベント全文検索",
    helper: "このイベントの確定した原文・翻訳テキストを横断検索します。",
    placeholder: "キーワードまたは発言",
    search: "検索", more: "さらに表示", noResults: "該当する発言がありません。",
    error: "全文検索に失敗しました。", showSession: "セッションを開く",
    original: "原文",
  },
};

