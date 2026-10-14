import type { Locale } from "./locales";

export const POST_EVENT_COPY: Record<Locale, {
  heading: string; detail: string; upload: string; importLabel: string;
  summary: string; generate: string; refresh: string; generating: string;
  noSummary: string; failure: string; imported: string; warning: string;
  recording: string; selected: string;
  summaryStale: string; summaryLanguage: string; summaryProvider: string;
  korean: string; english: string; japanese: string; localProvider: string;
  externalProvider: string; externalConsent: string; modelId: string;
  defaultModel: string; exportSummary: string;
}> = {
  en: {
    heading: "Event archive", detail: "Transcript, optional recording, and AI summary are linked to this session.",
    upload: "Import WAV recording", importLabel: "Import an authorized, uncompressed 16-bit WAV (max 512 MiB). With explicit opt-in, live WAV recordings are also available after the session.",
    summary: "AI event summary", generate: "Generate / retry summary", refresh: "Refresh archive",
    generating: "Generating from the final source transcript…", noSummary: "No summary generated.",
    failure: "Archive request failed.", imported: "Recording imported.", warning: "This session ended after an interruption. Verify the transcript before sharing the summary.",
    recording: "Recording", selected: "Choose WAV recording",
    summaryStale: "The source transcript changed after this summary was generated. Review and regenerate before sharing.",
    summaryLanguage: "Summary language", summaryProvider: "Summary provider",
    korean: "Korean", english: "English", japanese: "Japanese",
    localProvider: "Local Ollama", externalProvider: "External OpenAI-compatible",
    externalConsent: "I explicitly approve sending this transcript to the configured external provider.",
    modelId: "Model ID (optional)", defaultModel: "Configured default model",
    exportSummary: "Export summary text",
  },
  ko: {
    heading: "행사 아카이브", detail: "원문 전사문·녹음·AI 요약을 동일한 세션에서 관리합니다.",
    upload: "WAV 녹음 파일 가져오기", importLabel: "사용 권한이 있는 16비트 비압축 WAV를 선택하세요(최대 512MiB). 명시적으로 동의한 자동 녹음 파일도 종료 후 표시됩니다.",
    summary: "AI 행사 요약", generate: "요약 생성 / 다시 생성", refresh: "기록 새로고침",
    generating: "최종 원문 전사문으로 요약을 생성 중입니다…", noSummary: "생성된 요약이 없습니다.",
    failure: "아카이브 요청을 처리하지 못했습니다.", imported: "녹음 파일을 연결했습니다.", warning: "비정상 종료된 세션입니다. 요약 공유 전에 전사 누락을 확인하세요.",
    recording: "녹음 파일", selected: "WAV 녹음 파일 선택",
    summaryStale: "요약 생성 이후 원문 전사문이 변경되었습니다. 공유 전 확인하고 요약을 다시 생성하세요.",
    summaryLanguage: "요약 언어", summaryProvider: "요약 엔진",
    korean: "한국어", english: "영어", japanese: "일본어",
    localProvider: "로컬 Ollama", externalProvider: "외부 OpenAI 호환 API",
    externalConsent: "이 전사문을 설정된 외부 Provider로 전송하는 데 명시적으로 동의합니다.",
    modelId: "모델 ID(선택)", defaultModel: "설정된 기본 모델",
    exportSummary: "요약 텍스트 내보내기",
  },
  ja: {
    heading: "イベントアーカイブ", detail: "原文・録音・AI要約をセッション単位で管理します。",
    upload: "WAV録音を取り込む", importLabel: "権利を持つ16-bit非圧縮WAV（最大512MiB）。事前に同意した自動録音もセッション終了後に表示されます。",
    summary: "AIイベント要約", generate: "要約の生成・再生成", refresh: "アーカイブ更新",
    generating: "確定した原文から要約を生成中…", noSummary: "要約は未生成です。",
    failure: "アーカイブ処理に失敗しました。", imported: "録音を保存しました。", warning: "中断したセッションです。共有前に原文の欠落を確認してください。",
    recording: "録音", selected: "WAV録音を選択",
    summaryStale: "要約作成後に原文が更新されています。共有前に確認し、要約を再生成してください。",
    summaryLanguage: "要約言語", summaryProvider: "要約エンジン",
    korean: "韓国語", english: "英語", japanese: "日本語",
    localProvider: "ローカルOllama", externalProvider: "外部OpenAI互換API",
    externalConsent: "この原文を設定済みの外部プロバイダーへ送信することに明示的に同意します。",
    modelId: "モデルID（任意）", defaultModel: "設定済みの既定モデル",
    exportSummary: "要約テキストを書き出す",
  },
};

