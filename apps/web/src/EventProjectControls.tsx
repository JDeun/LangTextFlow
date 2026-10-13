import { useState } from "react";
import type { EventProject } from "./types";
import type { Locale } from "./locales";
import { EVENT_PROJECT_COPY } from "./eventProjectCopy";
import "./eventProjects.css";

type Status = EventProject["status"];


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
  const t = EVENT_PROJECT_COPY[locale];
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
