import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  LOCALE_STORAGE_KEY,
  SUPPORTED_LOCALES,
  resolveInitialLocale,
  type Locale,
} from "./locales";

const en = {
  app: {
    tagline: "Local-first realtime multilingual captioning",
    setup: "Setup",
    live: "Live",
    history: "History",
    serverConnected: "Server connected",
    serverConnecting: "Connecting to server",
    language: "Interface language",
  },
  audience: {
    invalidSession: "This session is invalid or has ended.",
    loading: "Loading session…",
    captionLanguage: "Caption language",
    source: "Source",
    connecting: "Connecting",
  },
  session: {
    settings: "Session setup",
    title: "Session title",
    presenter: "Presenter / speaker",
    optional: "Optional",
    preset: "Use case",
    sourceLanguage: "Source language",
    sourceHelp: "The source language is always available as the original caption.",
    hotwords: "Important terms / Hotwords",
    hotwordsHelp: "Session-only hints. Save frequently used terms in the glossary below.",
    start: "Start session",
    stop: "Stop captions",
    busy: "Working…",
    newTitle: "New live caption session",
  },
  preset: {
    general: "General",
    church: "Church / Mission gathering",
    conference: "Conference",
    lecture: "Lecture",
  },
  audio: {
    input: "Audio input",
    defaultDevice: "Default input device",
    findDevice: "Find devices first",
    refresh: "Refresh microphone permission / devices",
    noDevice: "No available audio input device.",
    deviceError: "Could not find audio devices.",
  },
  engines: {
    asr: "Speech recognition engine",
    autoHelp: "Falls back to local faster-whisper if VibeVoice fails to start.",
    correction: "Context-aware LLM correction",
    correctionHelp: "LLM failures, timeouts, or excessive rewrites are rejected automatically.",
    correctionModel: "Ollama correction model",
    translation: "Translation engine",
    translationModel: "Ollama translation model",
    apiModel: "API translation model ID",
  },
  live: {
    status: "Realtime status",
    preview: "Audience preview",
    displayLanguage: "Display language",
    segments: "Live segments",
    emptySegments: "No segments received yet.",
    emptyCaption: "Start speaking and captions will appear here.",
    persistenceWarning: "Recording warning",
  },
  preflight: {
    requestFailed: "Preflight request failed.",
    needsSetup: "Setup is required before starting",
    checkSystem: "Review the system preflight checks.",
  },
  error: {
    startFailed: "Could not start the session.",
    stopFailed: "Could not stop the session.",
  },
  locale: {
    en: "English",
    ko: "한국어",
    ja: "日本語",
  },
} as const;

type WidenStrings<T> = T extends string ? string : { [K in keyof T]: WidenStrings<T[K]> };

type Catalog = WidenStrings<typeof en>;

const ko: Catalog = {
  app: {
    tagline: "로컬 우선 실시간 다국어 자막",
    setup: "설정",
    live: "라이브",
    history: "기록",
    serverConnected: "서버 연결됨",
    serverConnecting: "서버 연결 중",
    language: "인터페이스 언어",
  },
  audience: {
    invalidSession: "유효하지 않거나 종료된 세션입니다.",
    loading: "세션을 불러오는 중입니다…",
    captionLanguage: "자막 언어",
    source: "원문",
    connecting: "연결 중",
  },
  session: {
    settings: "세션 설정",
    title: "세션 이름",
    presenter: "발표자 / 강사",
    optional: "선택 사항",
    preset: "사용 목적",
    sourceLanguage: "입력 언어",
    sourceHelp: "입력 언어는 원문 자막으로 항상 제공됩니다.",
    hotwords: "중요 용어 / Hotwords",
    hotwordsHelp: "일회성 세션 힌트입니다. 반복 사용할 용어는 아래 용어집에 저장하세요.",
    start: "세션 시작",
    stop: "자막 중지",
    busy: "처리 중…",
    newTitle: "새 실시간 자막 세션",
  },
  preset: {
    general: "일반",
    church: "교회 / 선교 집회",
    conference: "컨퍼런스",
    lecture: "강의",
  },
  audio: {
    input: "오디오 입력",
    defaultDevice: "기본 입력 장치",
    findDevice: "장치를 먼저 찾으세요",
    refresh: "마이크 권한 / 장치 새로고침",
    noDevice: "사용 가능한 오디오 입력 장치가 없습니다.",
    deviceError: "오디오 장치를 찾지 못했습니다.",
  },
  engines: {
    asr: "음성 인식 엔진",
    autoHelp: "VibeVoice 시작 실패 시 로컬 faster-whisper로 자동 전환합니다.",
    correction: "문맥 기반 LLM 보정",
    correctionHelp: "LLM 실패·timeout·과도한 수정은 자동 거부하고 규칙 기반 결과를 사용합니다.",
    correctionModel: "Ollama 보정 모델",
    translation: "번역 엔진",
    translationModel: "Ollama 번역 모델",
    apiModel: "API 번역 모델 ID",
  },
  live: {
    status: "실시간 상태",
    preview: "Audience Preview",
    displayLanguage: "표시 언어",
    segments: "실시간 세그먼트",
    emptySegments: "아직 수신된 세그먼트가 없습니다.",
    emptyCaption: "말하기를 시작하면 자막이 이곳에 표시됩니다.",
    persistenceWarning: "기록 저장 경고",
  },
  preflight: {
    requestFailed: "사전점검 요청에 실패했습니다.",
    needsSetup: "시작 전 준비가 필요합니다",
    checkSystem: "시스템 사전점검을 확인하세요.",
  },
  error: {
    startFailed: "세션을 시작하지 못했습니다.",
    stopFailed: "세션을 중지하지 못했습니다.",
  },
  locale: {
    en: "English",
    ko: "한국어",
    ja: "日本語",
  },
};

const ja: Catalog = {
  app: {
    tagline: "ローカルファーストのリアルタイム多言語字幕",
    setup: "設定",
    live: "ライブ",
    history: "履歴",
    serverConnected: "サーバー接続済み",
    serverConnecting: "サーバー接続中",
    language: "表示言語",
  },
  audience: {
    invalidSession: "無効または終了したセッションです。",
    loading: "セッションを読み込んでいます…",
    captionLanguage: "字幕言語",
    source: "原文",
    connecting: "接続中",
  },
  session: {
    settings: "セッション設定",
    title: "セッション名",
    presenter: "発表者 / 講師",
    optional: "任意",
    preset: "用途",
    sourceLanguage: "入力言語",
    sourceHelp: "入力言語は原文字幕として常に表示できます。",
    hotwords: "重要用語 / Hotwords",
    hotwordsHelp: "このセッション専用のヒントです。繰り返し使う用語は用語集に保存してください。",
    start: "セッション開始",
    stop: "字幕を停止",
    busy: "処理中…",
    newTitle: "新しいリアルタイム字幕セッション",
  },
  preset: {
    general: "一般",
    church: "教会 / 宣教集会",
    conference: "カンファレンス",
    lecture: "講義",
  },
  audio: {
    input: "音声入力",
    defaultDevice: "既定の入力デバイス",
    findDevice: "先にデバイスを検索してください",
    refresh: "マイク権限 / デバイスを更新",
    noDevice: "利用可能な音声入力デバイスがありません。",
    deviceError: "音声デバイスを検出できませんでした。",
  },
  engines: {
    asr: "音声認識エンジン",
    autoHelp: "VibeVoice の起動に失敗した場合、ローカルの faster-whisper に自動で切り替えます。",
    correction: "コンテキスト対応 LLM 補正",
    correctionHelp: "LLM の失敗、タイムアウト、過剰な書き換えは自動で拒否します。",
    correctionModel: "Ollama 補正モデル",
    translation: "翻訳エンジン",
    translationModel: "Ollama 翻訳モデル",
    apiModel: "API 翻訳モデル ID",
  },
  live: {
    status: "リアルタイム状態",
    preview: "視聴者プレビュー",
    displayLanguage: "表示言語",
    segments: "ライブセグメント",
    emptySegments: "受信したセグメントはまだありません。",
    emptyCaption: "話し始めると、ここに字幕が表示されます。",
    persistenceWarning: "記録の警告",
  },
  preflight: {
    requestFailed: "事前チェックに失敗しました。",
    needsSetup: "開始前に設定が必要です",
    checkSystem: "システムの事前チェックを確認してください。",
  },
  error: {
    startFailed: "セッションを開始できませんでした。",
    stopFailed: "セッションを停止できませんでした。",
  },
  locale: {
    en: "English",
    ko: "한국어",
    ja: "日本語",
  },
};

const catalogs: Record<Locale, Catalog> = { en, ko, ja };

type DotPath<T> = T extends string
  ? never
  : {
      [K in keyof T & string]: T[K] extends string ? K : `${K}.${DotPath<T[K]>}`;
    }[keyof T & string];

export type TranslationKey = DotPath<Catalog>;

function readPath(catalog: Catalog, key: string): string {
  let value: unknown = catalog;
  for (const part of key.split(".")) {
    if (!value || typeof value !== "object") return key;
    value = (value as Record<string, unknown>)[part];
  }
  return typeof value === "string" ? value : key;
}

function detectLocale(): Locale {
  return resolveInitialLocale(
    window.localStorage.getItem(LOCALE_STORAGE_KEY),
    window.navigator.language,
  );
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey) => string;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  }, [locale]);

  const value = useMemo<LocaleContextValue>(
    () => ({
      locale,
      setLocale: setLocaleState,
      t: (key) => readPath(catalogs[locale], key),
    }),
    [locale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useI18n() {
  const value = useContext(LocaleContext);
  if (!value) throw new Error("useI18n must be used inside LocaleProvider");
  return value;
}

export function LanguageSwitcher({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className={`locale-switcher ${compact ? "compact" : ""}`}>
      {!compact && <span>{t("app.language")}</span>}
      <select
        aria-label={t("app.language")}
        data-testid="locale-select"
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
      >
        {SUPPORTED_LOCALES.map((code) => (
          <option key={code} value={code}>{t(`locale.${code}` as TranslationKey)}</option>
        ))}
      </select>
    </label>
  );
}
