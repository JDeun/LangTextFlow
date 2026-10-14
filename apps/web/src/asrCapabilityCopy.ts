import type { AsrBlockReason } from "./asrCapabilities";
export const asrAvailabilityCopy: Record<
  "ko" | "en" | "ja", Record<Exclude<AsrBlockReason, null>, string>
> = {
  ko: {
    registry: "음성 인식 엔진 기능 목록을 확인할 수 없습니다. 세션 시작을 차단합니다.",
    unknown: "현재 서버에서 이 음성 인식 엔진을 지원하지 않습니다.",
    language: "이 음성 인식 엔진은 선택한 입력 언어를 지원한다고 선언하지 않았습니다.",
  },
  en: {
    registry: "ASR provider discovery unavailable; session start blocked.",
    unknown: "ASR provider not declared by this server.",
    language: "The provider does not declare support for this source language.",
  },
  ja: {
    registry: "音声認識エンジン一覧を取得できません。セッション開始を停止します。",
    unknown: "この音声認識エンジンはサーバーで未対応です。",
    language: "この音声認識エンジンに入力言語の対応宣言がありません。",
  },
};
