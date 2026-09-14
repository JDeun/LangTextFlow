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
  cacheHeadroom: string;
  cacheReserve: string;
  clear: string;
  updateTitle: string;
  updateHelp: string;
  updateCheck: string;
  updateChecking: string;
  updateCurrent: string;
  updateAvailable: string;
  updateInstall: string;
  updateInstalling: string;
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
    cacheHeadroom: "Safe writable space",
    cacheReserve: "Reserved free space",
    clear: "Clear cache",
    updateTitle: "Desktop update",
    updateHelp: "Signed release builds verify the update package before the current installation is changed.",
    updateCheck: "Check for update",
    updateChecking: "Checking…",
    updateCurrent: "This version is current.",
    updateAvailable: "Update available",
    updateInstall: "Verify & install",
    updateInstalling: "Verifying and installing…",
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
    cacheHeadroom: "안전하게 쓸 수 있는 공간",
    cacheReserve: "보호용 여유 공간",
    clear: "캐시 삭제",
    updateTitle: "데스크톱 업데이트",
    updateHelp: "서명된 배포본은 업데이트 파일 검증이 끝나기 전까지 현재 설치를 변경하지 않습니다.",
    updateCheck: "업데이트 확인",
    updateChecking: "확인 중…",
    updateCurrent: "현재 최신 버전입니다.",
    updateAvailable: "업데이트 사용 가능",
    updateInstall: "검증 후 설치",
    updateInstalling: "검증 및 설치 중…",
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
    cacheHeadroom: "安全に書き込める空き容量",
    cacheReserve: "保護用の予約空き容量",
    clear: "キャッシュを削除",
    updateTitle: "デスクトップ更新",
    updateHelp: "署名済みリリースでは更新ファイルの検証が完了するまで現在のインストールを変更しません。",
    updateCheck: "更新を確認",
    updateChecking: "確認中…",
    updateCurrent: "現在のバージョンは最新です。",
    updateAvailable: "更新があります",
    updateInstall: "検証してインストール",
    updateInstalling: "検証・インストール中…",
    recommendationsTitle: "用語集の候補",
    recommendationsHelp: "最近のセッションで繰り返された未登録の用語です。",
    recommendationsEmpty: "繰り返された未登録用語はまだありません。",
    add: "追加",
    occurrences: "回",
    sessions: "セッション",
  },
};
