import type { Locale } from "./locales";

export const AUDIENCE_METRICS_COPY: Record<Locale, {
  operatorConsent: string; audienceConsent: string; heading: string; disabled: string;
  empty: string; detail: string;
}> = {
  en: {
    operatorConsent: "Allow optional audience display timing reports for this session",
    audienceConsent: "Share caption display timing with the operator (optional)",
    heading: "Audience display timing (estimated)", disabled: "Audience timing collection is off.",
    empty: "No opted-in audience reports in the last minute.",
    detail: "Last 60 seconds, up to 512 samples per stage. No captions, identifiers or client timestamps are sent. Double-rAF estimates include sampling bias and do not measure audio-to-screen delay.",
  },
  ko: {
    operatorConsent: "이번 세션에서 청중의 선택적 화면 지연 보고 허용",
    audienceConsent: "운영자에게 자막 표시 지연 보내기(선택)",
    heading: "청중 화면 표시 지연(추정)", disabled: "청중 지연 수집이 꺼져 있습니다.",
    empty: "최근 1분 동안 동의한 청중의 보고가 없습니다.",
    detail: "최근 60초, 단계별 최대 512개 표본입니다. 자막·식별자·클라이언트 시각은 전송하지 않습니다. 이중 rAF 추정에는 표본 편향이 있으며 음성 입력부터 화면까지의 지연은 아닙니다.",
  },
  ja: {
    operatorConsent: "このセッションで聴衆の任意の表示時間レポートを許可",
    audienceConsent: "字幕表示時間を運営者と共有する（任意）",
    heading: "聴衆の表示時間（推定）", disabled: "聴衆の時間収集はオフです。",
    empty: "直近1分に同意済みの聴衆からの報告はありません。",
    detail: "直近60秒、段階ごとに最大512サンプル。字幕・識別子・端末時刻は送信しません。二重rAF推定には標本の偏りがあり、音声入力から画面までの遅延ではありません。",
  },
};
