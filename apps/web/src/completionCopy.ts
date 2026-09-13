import type { Locale } from "./locales";

export const COMPLETION_COPY: Record<Locale, {
  runtimeTitle: string;
  runtimeHelp: string;
  runtimeReady: string;
  runtimeMissing: string;
  install: string;
  installing: string;
  refresh: string;
  cacheTitle: string;
  cacheHelp: string;
  clear: string;
  recommendationsTitle: string;
  recommendationsHelp: string;
  recommendationsEmpty: string;
  add: string;
  occurrences: string;
  sessions: string;
}> = {
  en: {
    runtimeTitle: "Runtime & offline models",
    runtimeHelp: "Prepare local runtimes and inspect model cache used by this installation.",
    runtimeReady: "Ready",
    runtimeMissing: "Not ready",
    install: "Prepare",
    installing: "Preparing…",
    refresh: "Refresh",
    cacheTitle: "Offline model cache",
    cacheHelp: "Cached models stay available without downloading them again.",
    clear: "Clear cache",
    recommendationsTitle: "Glossary suggestions",
    recommendationsHelp: "Repeated terms from recent sessions that are not yet registered.",
    recommendationsEmpty: "No repeated unregistered terms found yet.",
    add: "Add",
    occurrences: "occurrences",
    sessions: "sessions",
  },
  ko: {
    runtimeTitle: "런타임 및 오프라인 모델",
    runtimeHelp: "이 설치에서 사용할 로컬 런타임을 준비하고 모델 캐시를 확인합니다.",
    runtimeReady: "준비됨",
    runtimeMissing: "준비 필요",
    install: "준비",
    installing: "준비 중…",
    refresh: "새로고침",
    cacheTitle: "오프라인 모델 캐시",
    cacheHelp: "캐시된 모델은 다시 다운로드하지 않고 오프라인에서 사용할 수 있습니다.",
    clear: "캐시 삭제",
    recommendationsTitle: "용어집 추천",
    recommendationsHelp: "최근 세션에서 반복됐지만 아직 등록되지 않은 용어입니다.",
    recommendationsEmpty: "아직 반복된 미등록 용어가 없습니다.",
    add: "추가",
    occurrences: "회 등장",
    sessions: "개 세션",
  },
  ja: {
    runtimeTitle: "ランタイムとオフラインモデル",
    runtimeHelp: "このインストールで使うローカルランタイムを準備し、モデルキャッシュを確認します。",
    runtimeReady: "準備完了",
    runtimeMissing: "準備が必要",
    install: "準備",
    installing: "準備中…",
    refresh: "更新",
    cacheTitle: "オフラインモデルキャッシュ",
    cacheHelp: "キャッシュ済みモデルは再ダウンロードせずオフラインで利用できます。",
    clear: "キャッシュを削除",
    recommendationsTitle: "用語集の候補",
    recommendationsHelp: "最近のセッションで繰り返された未登録の用語です。",
    recommendationsEmpty: "繰り返された未登録用語はまだありません。",
    add: "追加",
    occurrences: "回",
    sessions: "セッション",
  },
};
