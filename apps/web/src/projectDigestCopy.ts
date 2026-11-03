import type { Locale } from "./locales";

export const PROJECT_DIGEST_COPY: Record<Locale, {
  title: string; scope: string; prerequisite: string; included: string; excluded: string;
  interrupted: string; versions: string; none: string; generate: string; stale: string;
  unavailable: string; evidence: string; export: string; source: string; limit: string;
}> = {
  ko: {
    title: "프로젝트 요약 · 연대기", scope: "완료된 세션의 현재 요약과 그 원문을 함께 검토하는 별도 AI 초안입니다. 페이지 목록과 달리 프로젝트 전체를 사용합니다.",
    prerequisite: "각 완료 세션에서 현재 원문을 인용하는 요약을 먼저 생성하세요. 원문이나 승인본이 변경되었다면 해당 세션 요약을 다시 생성하세요.",
    included: "근거에 포함된 완료 세션", excluded: "진행 중이라 제외된 세션", interrupted: "중단 후 복구된 세션이 포함됩니다. 누락된 발화가 있을 수 있습니다.",
    versions: "프로젝트 요약 버전", none: "아직 프로젝트 요약이 없습니다.", generate: "프로젝트 요약 생성 / 다시 시도", stale: "프로젝트 근거가 변경되었습니다. 새 버전을 생성한 뒤 검수하세요. 기존 승인 내용은 보존됩니다.",
    unavailable: "세션 연결이 변경되어 이 버전의 내용을 표시하거나 내보낼 수 없습니다. 현재 프로젝트에서 새 버전을 생성하세요.",
    evidence: "이 버전에 고정된 원문 근거", export: "버전 · 근거 · 검수 이력 JSON 저장", source: "근거 세션 열기", limit: "최대 25개 세션, 원문 6,000구간, 원문·요약 자료 합계 240,000자입니다. 초과 시 프로젝트 범위를 줄여주세요. 전체 미디어 백업·복원 기능은 아닙니다.",
  },
  en: {
    title: "Project digest · chronology", scope: "A separate AI draft combines current summaries and their original evidence from completed sessions. It uses the whole project, rather than the visible archive page.",
    prerequisite: "First generate a current source-cited summary for every finished session. Regenerate session summaries when original evidence or the approved transcript changes.",
    included: "Completed sessions included", excluded: "Running sessions excluded", interrupted: "Recovered interrupted sessions are included; some speech may be missing.",
    versions: "Project digest version", none: "No project digest yet.", generate: "Generate / retry project digest", stale: "Project evidence changed. Generate a new version before review. Existing approved text is preserved.",
    unavailable: "Session membership changed. This version cannot be displayed or exported here; generate a new version for the current project.",
    evidence: "Original evidence frozen for this version", export: "Export version, evidence and reviews as JSON", source: "Open source session", limit: "At most 25 sessions, 6,000 source segments and 240,000 combined source/note characters. Reduce the project scope if exceeded. This is not a full media backup or restore.",
  },
  ja: {
    title: "プロジェクト要約・時系列", scope: "終了したセッションの現在の要約と原文を組み合わせる独立したAI下書きです。表示中の一覧ページではなく、プロジェクト全体を使用します。",
    prerequisite: "終了した各セッションで現在の原文を引用する要約を先に生成してください。原文や承認済み文字起こしが変わった場合は再生成してください。",
    included: "含まれる終了セッション", excluded: "実行中のため除外されたセッション", interrupted: "中断後に復旧したセッションを含みます。欠落した発言がある可能性があります。",
    versions: "プロジェクト要約の版", none: "プロジェクト要約はまだありません。", generate: "プロジェクト要約を生成・再試行", stale: "プロジェクトの根拠が変更されました。新しい版を生成してから確認してください。既存の承認内容は保持されます。",
    unavailable: "セッションの所属が変わったため、この版を表示・出力できません。現在のプロジェクトで新しい版を生成してください。",
    evidence: "この版に固定された原文", export: "版・根拠・確認履歴をJSON保存", source: "根拠セッションを開く", limit: "最大25セッション、原文6,000区間、原文と要約資料の合計240,000文字です。超過したら範囲を縮小してください。メディア全体のバックアップ・復元ではありません。",
  },
};
