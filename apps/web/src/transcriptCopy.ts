import type { Locale } from "./locales";

export const TRANSCRIPT_COPY: Record<Locale, {
  heading: string; detail: string; recording: string; model: string; language: string;
  generate: string; cancel: string; version: string; none: string; review: string;
  original: string; save: string; approve: string; approved: string; draft: string;
  failure: string; source: string; live: string; approvedSource: string; open: string;
  running: string; completed: string; error: string; cancelled: string;
  seekRecording: string;
}> = {
  en: {
    heading: "Post-event transcript", detail: "Re-transcribe a managed recording locally with an already cached model. Live captions stay intact. Compare the recording before approving; approval locks this version.",
    recording: "Source recording", model: "Local ASR model", language: "Speech language",
    generate: "Create transcript draft", cancel: "Cancel transcription", version: "Transcript version",
    none: "No post-event transcript.", review: "Review transcript", original: "Original ASR draft and provenance",
    save: "Save transcript review", approve: "Approve and lock transcript", approved: "Approved · locked",
    draft: "Unapproved draft", failure: "Transcript request failed.", source: "Summary source",
    live: "Original live ASR", approvedSource: "Approved post-event transcript", open: "Review transcript source",
    running: "Transcribing", completed: "Completed", error: "Failed", cancelled: "Cancelled",
    seekRecording: "Seek source recording (estimated time)",
  },
  ko: {
    heading: "행사 후 정밀 전사", detail: "이미 내려받은 로컬 모델로 보관된 녹음을 다시 전사합니다. 실시간 자막은 유지됩니다. 녹음과 대조한 뒤 승인하세요. 승인한 버전은 잠깁니다.",
    recording: "원본 녹음", model: "로컬 ASR 모델", language: "음성 언어",
    generate: "전사 초안 생성", cancel: "전사 취소", version: "전사 버전",
    none: "행사 후 전사문이 없습니다.", review: "전사문 검수", original: "ASR 원본 초안과 처리 정보",
    save: "전사 검수 저장", approve: "전사 승인 및 잠금", approved: "승인 완료 · 잠김",
    draft: "미승인 초안", failure: "전사 요청을 처리하지 못했습니다.", source: "요약 원문",
    live: "실시간 ASR 원문", approvedSource: "승인한 행사 후 전사문", open: "요약의 전사 원문 확인",
    running: "전사 중", completed: "완료", error: "실패", cancelled: "취소됨",
    seekRecording: "원본 녹음 위치 이동 (추정 시간)",
  },
  ja: {
    heading: "イベント後の文字起こし", detail: "キャッシュ済みのローカルモデルで保存録音を再処理します。ライブ字幕は保持されます。録音と照合して承認してください。承認した版はロックされます。",
    recording: "元の録音", model: "ローカルASRモデル", language: "音声言語",
    generate: "文字起こしの下書きを作成", cancel: "文字起こしを中止", version: "文字起こしの版",
    none: "イベント後の文字起こしはありません。", review: "文字起こしを確認", original: "ASRの元の下書きと処理情報",
    save: "レビューを保存", approve: "文字起こしを承認してロック", approved: "承認済み・ロック",
    draft: "未承認の下書き", failure: "文字起こしの処理に失敗しました。", source: "要約の原文",
    live: "ライブASRの原文", approvedSource: "承認済みのイベント後の原文", open: "要約の原文を確認",
    running: "処理中", completed: "完了", error: "失敗", cancelled: "中止済み",
    seekRecording: "元の録音へ移動（推定時刻）",
  },
};
