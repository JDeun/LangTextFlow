import type { Locale } from "./locales";

const en = {
  context: {
    badType: "Only TXT, Markdown, PDF, and DOCX files are supported.", empty: "Empty files are not supported.", tooLarge: "Maximum file size is 5 MB.",
    maxDocs: "Reference documents are limited to", readFailed: "Could not read the reference document.", title: "Reference documents",
    help: "Use terms and context from sermons or presentation materials as ASR and translation hints.", reading: "Reading…", add: "Add files",
    note: "TXT/MD/PDF/DOCX · 5 MB per file · up to 4 files. OCR for scanned PDFs is not supported yet.", extracted: "characters extracted",
    truncated: "up to 60,000 characters used", localExtract: "extracted locally when the session starts", remove: "Remove",
  },
  model: {
    asrModel: "ASR model", translationModel: "translation model", predownload: "download in advance", download: "download",
    stateHttp: "Model setup status HTTP", stateFailed: "Could not check model setup status.", startFailed: "Could not start the model download.",
    cancelFailed: "Could not cancel the download request.", preparing: "Preparing…", cancelling: "Cancelling…", cancel: "Cancel download request", retry: "Try again",
  },
  vibe: {
    statusHttp: "VibeVoice status HTTP", statusFailed: "Could not check VibeVoice status.", actionFailed: "failed",
    external: "External sidecar connected", managed: "Managed sidecar READY", starting: "Starting sidecar", stopped: "Sidecar can be started",
    unconfigured: "Managed execution not configured", error: "Sidecar error", checking: "Checking status", logs: "Recent runtime logs",
    startRequest: "Requesting start…", start: "Start VibeVoice", stopRequest: "Requesting stop…", cancelStart: "Cancel startup", stop: "Stop VibeVoice", refresh: "Refresh status",
  },
  telemetry: {
    unavailable: "telemetry unavailable", diagnosticsFailed: "Could not create diagnostics bundle.", title: "Realtime telemetry",
    collecting: "Collecting diagnostics…", diagnostics: "Diagnostics bundle", provider: "ASR provider", recovered: "recovered", failover: "failover",
    handoff: "provider handoff", healthy: "provider healthy", notRunning: "provider not running", idle: "session idle", voice: "Voice detected", noVoice: "No voice",
    audio: "audio", asrQueue: "ASR queue", peak: "peak", failovers: "ASR failovers", recoveredHandoff: "recovered handoff", none: "none",
    postprocess: "Postprocess", correctionTranslation: "correction + translation", storage: "Storage queue", writer: "SQLite writer", enqueue: "Audio enqueue",
    backpressure: "backpressure", lag: "ASR lag", lagHelp: "audio end → stable", correction: "Correction", correctionHelp: "stable → corrected",
    translation: "Translation", translationHelp: "corrected → translated", commit: "Commit", commitHelp: "stable → committed",
  },
} as const;

type Widen<T> = T extends string ? string : { [K in keyof T]: Widen<T[K]> };
type UtilityCopy = Widen<typeof en>;

const ko: UtilityCopy = {
  context: {
    badType: "TXT, Markdown, PDF, DOCX 파일만 사용할 수 있습니다.", empty: "빈 파일은 사용할 수 없습니다.", tooLarge: "파일당 최대 크기는 5 MB입니다.",
    maxDocs: "참고 문서는 최대", readFailed: "참고 문서를 읽지 못했습니다.", title: "참고 문서",
    help: "설교문·발표자료의 용어와 문맥을 ASR 및 번역 힌트로 사용합니다.", reading: "읽는 중…", add: "파일 추가",
    note: "TXT/MD/PDF/DOCX · 파일당 5 MB · 최대 4개. 스캔 PDF OCR은 아직 지원하지 않습니다.", extracted: "자 추출",
    truncated: "60,000자까지 사용", localExtract: "세션 시작 시 로컬 추출", remove: "제거",
  },
  model: {
    asrModel: "ASR 모델", translationModel: "번역 모델", predownload: "미리 다운로드", download: "다운로드",
    stateHttp: "모델 준비 상태 HTTP", stateFailed: "모델 준비 상태를 확인하지 못했습니다.", startFailed: "모델 다운로드를 시작하지 못했습니다.",
    cancelFailed: "다운로드 요청을 취소하지 못했습니다.", preparing: "준비 중…", cancelling: "취소 중…", cancel: "다운로드 요청 취소", retry: "다시 시도",
  },
  vibe: {
    statusHttp: "VibeVoice 상태 HTTP", statusFailed: "VibeVoice 상태를 확인하지 못했습니다.", actionFailed: "실패했습니다",
    external: "외부 sidecar 연결됨", managed: "관리형 sidecar READY", starting: "sidecar 시작 중", stopped: "sidecar 시작 가능",
    unconfigured: "관리형 실행 미구성", error: "sidecar 오류", checking: "상태 확인 중", logs: "최근 실행 로그",
    startRequest: "시작 요청 중…", start: "VibeVoice 시작", stopRequest: "중지 요청 중…", cancelStart: "시작 취소", stop: "VibeVoice 종료", refresh: "상태 새로고침",
  },
  telemetry: {
    unavailable: "telemetry unavailable", diagnosticsFailed: "진단 번들을 만들지 못했습니다.", title: "실시간 텔레메트리",
    collecting: "진단 수집 중…", diagnostics: "진단 번들", provider: "ASR provider", recovered: "복구됨", failover: "failover",
    handoff: "provider handoff", healthy: "provider 정상", notRunning: "provider 미실행", idle: "세션 대기", voice: "음성 감지", noVoice: "음성 없음",
    audio: "오디오", asrQueue: "ASR queue", peak: "최대", failovers: "ASR failovers", recoveredHandoff: "복구 전환", none: "없음",
    postprocess: "후처리", correctionTranslation: "보정 + 번역", storage: "저장 queue", writer: "SQLite writer", enqueue: "오디오 enqueue",
    backpressure: "backpressure", lag: "ASR 지연", lagHelp: "audio end → stable", correction: "보정", correctionHelp: "stable → corrected",
    translation: "번역", translationHelp: "corrected → translated", commit: "확정", commitHelp: "stable → committed",
  },
};

const ja: UtilityCopy = {
  context: {
    badType: "TXT、Markdown、PDF、DOCXファイルのみ使用できます。", empty: "空のファイルは使用できません。", tooLarge: "1ファイルの最大サイズは5 MBです。",
    maxDocs: "参考文書は最大", readFailed: "参考文書を読み込めませんでした。", title: "参考文書",
    help: "説教原稿や発表資料の用語と文脈をASR・翻訳のヒントとして使用します。", reading: "読み込み中…", add: "ファイル追加",
    note: "TXT/MD/PDF/DOCX · 1ファイル5 MB · 最大4件。スキャンPDFのOCRはまだ対応していません。", extracted: "文字抽出",
    truncated: "60,000文字まで使用", localExtract: "セッション開始時にローカル抽出", remove: "削除",
  },
  model: {
    asrModel: "ASRモデル", translationModel: "翻訳モデル", predownload: "事前ダウンロード", download: "ダウンロード",
    stateHttp: "モデル準備状態 HTTP", stateFailed: "モデル準備状態を確認できませんでした。", startFailed: "モデルのダウンロードを開始できませんでした。",
    cancelFailed: "ダウンロード要求をキャンセルできませんでした。", preparing: "準備中…", cancelling: "キャンセル中…", cancel: "ダウンロードをキャンセル", retry: "再試行",
  },
  vibe: {
    statusHttp: "VibeVoice 状態 HTTP", statusFailed: "VibeVoice の状態を確認できませんでした。", actionFailed: "に失敗しました",
    external: "外部sidecar接続済み", managed: "管理sidecar READY", starting: "sidecar起動中", stopped: "sidecar起動可能",
    unconfigured: "管理実行が未設定", error: "sidecarエラー", checking: "状態確認中", logs: "最近の実行ログ",
    startRequest: "起動要求中…", start: "VibeVoiceを起動", stopRequest: "停止要求中…", cancelStart: "起動をキャンセル", stop: "VibeVoiceを終了", refresh: "状態を更新",
  },
  telemetry: {
    unavailable: "telemetry unavailable", diagnosticsFailed: "診断バンドルを作成できませんでした。", title: "リアルタイムテレメトリ",
    collecting: "診断収集中…", diagnostics: "診断バンドル", provider: "ASR provider", recovered: "復旧", failover: "failover",
    handoff: "provider handoff", healthy: "provider正常", notRunning: "provider未実行", idle: "セッション待機", voice: "音声検出", noVoice: "音声なし",
    audio: "音声", asrQueue: "ASR queue", peak: "最大", failovers: "ASR failovers", recoveredHandoff: "復旧切替", none: "なし",
    postprocess: "後処理", correctionTranslation: "補正 + 翻訳", storage: "保存queue", writer: "SQLite writer", enqueue: "音声enqueue",
    backpressure: "backpressure", lag: "ASR遅延", lagHelp: "audio end → stable", correction: "補正", correctionHelp: "stable → corrected",
    translation: "翻訳", translationHelp: "corrected → translated", commit: "確定", commitHelp: "stable → committed",
  },
};

export const UTILITY_COPY: Record<Locale, UtilityCopy> = { en, ko, ja };
