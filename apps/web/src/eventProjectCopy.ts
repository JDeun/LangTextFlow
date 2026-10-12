import type { Locale } from "./locales";
import type { EventProject } from "./types";

type Status = EventProject["status"];

export const EVENT_PROJECT_COPY: Record<Locale, {
  heading: string; help: string; all: string; unassigned: string;
  create: string; save: string; cancel: string; edit: string; remove: string;
  title: string; description: string; date: string; location: string;
  status: string; confirmedDelete: string; failure: string; noProject: string;
  statuses: Record<Status, string>;
}> = {
  ko: {
    heading: "행사 프로젝트", help: "행사별로 세션·전사·녹음·요약을 묶어 관리합니다.",
    all: "전체 세션", unassigned: "미분류 세션",
    create: "프로젝트 만들기", save: "저장", cancel: "취소", edit: "프로젝트 편집", remove: "프로젝트 삭제",
    title: "행사명", description: "행사 설명", date: "행사 날짜", location: "장소", status: "상태",
    confirmedDelete: "프로젝트 연결만 삭제합니다. 전사문과 세션은 삭제되지 않습니다. 계속하시겠습니까?",
    failure: "프로젝트 작업에 실패했습니다.", noProject: "아직 프로젝트가 없습니다. 행사를 추가해 보세요.",
    statuses: { planning: "준비", active: "진행 중", completed: "종료", archived: "보관" },
  },
  en: {
    heading: "Event projects", help: "Organize sessions, transcripts, recordings and summaries by event.",
    all: "All sessions", unassigned: "Unassigned sessions",
    create: "Create project", save: "Save", cancel: "Cancel", edit: "Edit project", remove: "Delete project",
    title: "Event title", description: "Description", date: "Event date", location: "Location", status: "Status",
    confirmedDelete: "Delete this project association only? All sessions and transcripts will be preserved.",
    failure: "Could not update the project.", noProject: "No projects yet. Create an event to start organizing.",
    statuses: { planning: "Planning", active: "Active", completed: "Completed", archived: "Archived" },
  },
  ja: {
    heading: "イベントプロジェクト", help: "セッション・文字起こし・録音・要約をイベント単位で管理します。",
    all: "すべてのセッション", unassigned: "未分類セッション",
    create: "プロジェクトを作成", save: "保存", cancel: "キャンセル", edit: "編集", remove: "削除",
    title: "イベント名", description: "説明", date: "開催日", location: "場所", status: "状態",
    confirmedDelete: "関連付けのみ削除しますか？セッションと文字起こしは保持されます。",
    failure: "プロジェクトの更新に失敗しました。", noProject: "プロジェクトがありません。イベントを作成してください。",
    statuses: { planning: "準備", active: "進行中", completed: "終了", archived: "保管" },
  },
};

