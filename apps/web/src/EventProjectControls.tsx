import { useState } from "react";
import type { EventProject } from "./types";
import type { Locale } from "./locales";
import "./eventProjects.css";

type Status = EventProject["status"];

const COPY: Record<Locale, {
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

interface Props {
  apiUrl: string;
  locale: Locale;
  projects: EventProject[];
  selectedProjectId: string;
  onSelectProject: (projectId: string) => void;
  onProjectsChanged: () => Promise<void>;
}

export function EventProjectControls({
  apiUrl, locale, projects, selectedProjectId, onSelectProject, onProjectsChanged,
}: Props) {
  const t = COPY[locale];
  const current = projects.find((project) => project.project_id === selectedProjectId);
  const [mode, setMode] = useState<"closed" | "create" | "edit">("closed");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [eventDate, setEventDate] = useState("");
  const [location, setLocation] = useState("");
  const [status, setStatus] = useState<Status>("planning");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function startCreate() {
    setTitle("");
    setDescription("");
    setEventDate("");
    setLocation("");
    setStatus("planning");
    setError("");
    setMode("create");
  }

  function startEdit() {
    if (!current) return;
    setTitle(current.title);
    setDescription(current.description);
    setEventDate(current.event_date || "");
    setLocation(current.location);
    setStatus(current.status);
    setError("");
    setMode("edit");
  }

  async function save() {
    if (busy || !title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const url = mode === "edit" && current
        ? `${apiUrl}/api/v1/projects/${encodeURIComponent(current.project_id)}`
        : `${apiUrl}/api/v1/projects`;
      const response = await fetch(url, {
        method: mode === "edit" ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(), description,
          event_date: eventDate || null, location, status,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const project = (await response.json()) as EventProject;
      await onProjectsChanged();
      onSelectProject(project.project_id);
      setMode("closed");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t.failure);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!current || busy || !window.confirm(t.confirmedDelete)) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `${apiUrl}/api/v1/projects/${encodeURIComponent(current.project_id)}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await response.text());
      onSelectProject("all");
      setMode("closed");
      await onProjectsChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t.failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="event-project-workspace" aria-label={t.heading}>
      <div className="event-project-header">
        <div><strong>{t.heading}</strong><small>{t.help}</small></div>
        <button type="button" className="secondary-button" onClick={startCreate} disabled={busy}>
          {t.create}
        </button>
      </div>
      <div className="event-project-options" role="group" aria-label={t.heading}>
        <button type="button" className={selectedProjectId === "all" ? "selected" : ""} onClick={() => onSelectProject("all")}>
          {t.all}
        </button>
        {projects.map((project) => (
          <button
            type="button"
            key={project.project_id}
            className={selectedProjectId === project.project_id ? "selected" : ""}
            aria-pressed={selectedProjectId === project.project_id}
            onClick={() => onSelectProject(project.project_id)}
          >
            {project.title} <small>({project.session_count})</small>
          </button>
        ))}
        <button
          type="button"
          className={selectedProjectId === "unassigned" ? "selected" : ""}
          onClick={() => onSelectProject("unassigned")}
        >
          {t.unassigned}
        </button>
      </div>
      {projects.length === 0 && <small>{t.noProject}</small>}
      {current && mode === "closed" && (
        <div className="event-project-info">
          <span>{t.status}: {t.statuses[current.status]}</span>
          {current.event_date && <span>{t.date}: {current.event_date}</span>}
          {current.location && <span>{t.location}: {current.location}</span>}
          {current.description && <p>{current.description}</p>}
          <button type="button" className="secondary-button" onClick={startEdit}>{t.edit}</button>
          <button type="button" className="danger-quiet" onClick={() => void remove()}>{t.remove}</button>
        </div>
      )}
      {mode !== "closed" && (
        <form
          className="event-project-form"
          onSubmit={(event) => { event.preventDefault(); void save(); }}
        >
          <label>{t.title}<input maxLength={120} required value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          <label>{t.description}<textarea maxLength={8000} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <label>{t.date}<input type="date" value={eventDate} onChange={(event) => setEventDate(event.target.value)} /></label>
          <label>{t.location}<input maxLength={240} value={location} onChange={(event) => setLocation(event.target.value)} /></label>
          <label>{t.status}
            <select value={status} onChange={(event) => setStatus(event.target.value as Status)}>
              {(Object.keys(t.statuses) as Status[]).map((key) => (
                <option key={key} value={key}>{t.statuses[key]}</option>
              ))}
            </select>
          </label>
          <div className="event-project-buttons">
            <button type="submit" disabled={busy || !title.trim()}>{t.save}</button>
            <button type="button" className="secondary-button" onClick={() => setMode("closed")}>{t.cancel}</button>
          </div>
        </form>
      )}
      {error && <p className="error-box" role="alert">{error}</p>}
    </section>
  );
}
