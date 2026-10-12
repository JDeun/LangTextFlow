import type { Locale } from "./locales";

export const RECORDING_COPY: Record<Locale, {
  label: string; help: string; failed: string; active: string; ended: string;
}> = {
  en: {
    label: "Save the event audio recording (optional)",
    help: "Off by default. Confirm participant notice/consent first. Audio stays on this device and is linked to the completed session.",
    failed: "Recording failed",
    active: "Recording audio — saved to session history after stop.",
    ended: "Recording session ended — check the archive.",
  },
  ko: {
    label: "행사 음성 녹음 저장(선택)",
    help: "기본 비활성화입니다. 참가자 고지·동의를 확인한 뒤 선택하세요. 녹음은 기기 내부에 보관되며 종료 후 세션 기록에 연결됩니다.",
    failed: "녹음 실패",
    active: "음성 녹음 중 — 종료 후 세션 기록에 보관됩니다.",
    ended: "녹음 세션 종료 — 기록에서 확인하세요.",
  },
  ja: {
    label: "イベント音声を録音保存（任意）",
    help: "初期状態はオフです。参加者への告知・同意を確認してから有効にしてください。録音はローカル保存されます。",
    failed: "録音失敗",
    active: "録音中 — 終了後にセッション記録へ保存します。",
    ended: "録音終了 — セッション履歴で確認してください。",
  },
};
