import type { Locale } from "./locales";

export const PREFLIGHT_COPY: Record<Locale, {
  initial: string; checkingMic: string; http: string; failed: string; unsupported: string;
  noDevice: string; found: string; micFailed: string; ready: string; engineMic: string;
  configReady: string; needsCheck: string; pill: string; engineReady: string; checkNeeded: string;
  aria: string; beforeStart: string; collapse: string; recommendation: string; apply: string;
  fallbackPrep: string; missingCache: string; fallbackHelp: string; autoRepair: string;
  missingTranslation: string; translationHelp: string; microphone: string; rerun: string;
  checking: string; checkMic: string;
}> = {
  en: {
    initial: "Not checked yet.", checkingMic: "Checking microphone permission…", http: "System check HTTP",
    failed: "System check failed.", unsupported: "This browser does not support audio input.",
    noDevice: "No available audio input device.", found: "Audio inputs detected", micFailed: "Could not verify microphone permission or device.",
    ready: "Ready to use", engineMic: "Engines ready · microphone check required", configReady: "Current configuration ready", needsCheck: "Configuration needs attention",
    pill: "System check", engineReady: "engines ready", checkNeeded: "attention required", aria: "System preflight", beforeStart: "Before you start", collapse: "Collapse preflight panel",
    recommendation: "Recommended for this environment", apply: "Apply recommendation", fallbackPrep: "Prepare auto fallback", missingCache: "ASR model cache is missing.",
    fallbackHelp: "Download before the session to avoid delay during failover.", autoRepair: "Can be fixed automatically", missingTranslation: "Translation model is not installed yet.",
    translationHelp: "Download the selected model through the running Ollama instance.", microphone: "Microphone / audio input", rerun: "Run system check again",
    checking: "Checking…", checkMic: "Check microphone",
  },
  ko: {
    initial: "아직 확인하지 않았습니다.", checkingMic: "마이크 권한을 확인하는 중입니다…", http: "시스템 점검 HTTP",
    failed: "시스템 점검에 실패했습니다.", unsupported: "이 브라우저는 오디오 입력 API를 지원하지 않습니다.",
    noDevice: "사용 가능한 오디오 입력 장치가 없습니다.", found: "확인된 오디오 입력", micFailed: "마이크 권한 또는 장치를 확인하지 못했습니다.",
    ready: "사용 준비 완료", engineMic: "엔진 준비됨 · 마이크 확인 필요", configReady: "현재 구성 준비 완료", needsCheck: "설정 확인 필요",
    pill: "시스템 점검", engineReady: "엔진 준비", checkNeeded: "확인 필요", aria: "시스템 사전점검", beforeStart: "시작 전 점검", collapse: "점검 패널 접기",
    recommendation: "현재 환경 권장 구성", apply: "권장 구성 적용", fallbackPrep: "Auto fallback 준비", missingCache: "ASR 모델 cache가 없습니다.",
    fallbackHelp: "세션 전에 다운로드하면 failover 시 다운로드 지연 없이 전환할 수 있습니다.", autoRepair: "자동 해결 가능", missingTranslation: "번역 모델이 아직 없습니다.",
    translationHelp: "실행 중인 Ollama를 통해 선택한 모델을 내려받을 수 있습니다.", microphone: "마이크 / 오디오 입력", rerun: "시스템 다시 점검",
    checking: "점검 중…", checkMic: "마이크 점검",
  },
  ja: {
    initial: "まだ確認していません。", checkingMic: "マイク権限を確認しています…", http: "システムチェック HTTP",
    failed: "システムチェックに失敗しました。", unsupported: "このブラウザは音声入力APIをサポートしていません。",
    noDevice: "利用可能な音声入力デバイスがありません。", found: "検出した音声入力", micFailed: "マイク権限またはデバイスを確認できませんでした。",
    ready: "使用準備完了", engineMic: "エンジン準備完了 · マイク確認が必要", configReady: "現在の構成で準備完了", needsCheck: "設定の確認が必要",
    pill: "システムチェック", engineReady: "エンジン準備完了", checkNeeded: "確認が必要", aria: "システム事前チェック", beforeStart: "開始前チェック", collapse: "チェックパネルを折りたたむ",
    recommendation: "現在の環境の推奨構成", apply: "推奨構成を適用", fallbackPrep: "Auto fallbackを準備", missingCache: "ASRモデルキャッシュがありません。",
    fallbackHelp: "セッション前にダウンロードすると、failover時のダウンロード遅延を避けられます。", autoRepair: "自動解決可能", missingTranslation: "翻訳モデルがまだありません。",
    translationHelp: "実行中のOllamaから選択したモデルをダウンロードできます。", microphone: "マイク / 音声入力", rerun: "システムを再チェック",
    checking: "確認中…", checkMic: "マイクを確認",
  },
};
