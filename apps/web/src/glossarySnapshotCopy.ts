import type { Locale } from "./locales";

export const GLOSSARY_SNAPSHOT_COPY = {
  en: { title: "Session glossary preview", preview: "Review resolved glossary", loading: "Resolving glossary…",
    help: "Review global, preset, project and session terminology before starting. If reviewed terms change, start is rejected so you can review again.",
    pending: "Not reviewed for the current source language, preset and project.", failed: "Could not resolve the glossary. Retry before reviewing.",
    empty: "No terms apply to this session.", count: "terms", aliases: "Aliases" },
  ko: { title: "세션 용어집 미리보기", preview: "병합된 용어집 검토", loading: "용어집 확인 중…",
    help: "시작 전에 전역·프리셋·프로젝트·세션 용어를 검토하세요. 검토한 용어가 바뀌면 시작을 거절하므로 다시 검토할 수 있습니다.",
    pending: "현재 원문 언어·프리셋·프로젝트의 용어집을 아직 검토하지 않았습니다.", failed: "용어집을 확인하지 못했습니다. 다시 시도해 검토하세요.",
    empty: "이 세션에 적용되는 용어가 없습니다.", count: "개 용어", aliases: "별칭" },
  ja: { title: "セッション用語集のプレビュー", preview: "統合した用語集を確認", loading: "用語集を確認中…",
    help: "開始前に全体・プリセット・プロジェクト・セッションの用語を確認できます。確認した用語が変わると開始を拒否し、再確認できます。",
    pending: "現在の原文言語・プリセット・プロジェクトの用語集は未確認です。", failed: "用語集を取得できませんでした。再試行して確認してください。",
    empty: "このセッションに適用される用語はありません。", count: "用語", aliases: "別名" },
} satisfies Record<Locale, Record<string, string>>;
