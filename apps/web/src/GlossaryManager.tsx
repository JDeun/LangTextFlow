import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GLOSSARY_COPY } from "./glossaryCopy";
import {
  existingGlossaryDraft, glossaryPayload, GLOSSARY_PRESETS,
  newGlossaryDraft, type GlossaryDraft,
} from "./glossaryEditor";
import { useI18n } from "./i18n";
import type { GlossaryRecord, ProductPreset } from "./types";

interface GlossaryManagerProps {
  apiUrl: string;
  preset: ProductPreset;
  sourceLanguage: string;
  targetLanguage: string;
  disabled: boolean;
}

interface GlossaryImportResult {
  total: number;
  created: number;
  updated: number;
  skipped: number;
}

type ImportConflictPolicy = "upsert" | "skip";
type TransferFormat = "json" | "csv";

function downloadFilename(response: Response, fallback: string) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

export function GlossaryManager({
  apiUrl,
  preset,
  sourceLanguage,
  targetLanguage,
  disabled,
}: GlossaryManagerProps) {
  const { locale } = useI18n();
  const copy = GLOSSARY_COPY[locale];
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [entries, setEntries] = useState<GlossaryRecord[]>([]);
  const [draft, setDraft] = useState<GlossaryDraft>(() => newGlossaryDraft(preset, sourceLanguage));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [importPolicy, setImportPolicy] = useState<ImportConflictPolicy>("upsert");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/v1/glossary`);
    if (!response.ok) throw new Error(copy.loadFailed);
    setEntries((await response.json()) as GlossaryRecord[]);
  }, [apiUrl]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(reason.message));
  }, [refresh]);

  const applicableCount = useMemo(
    () => entries.filter(
      (entry) => entry.enabled && (entry.source_language ?? "ko") === sourceLanguage
        && (!entry.presets.length || entry.presets.includes(preset)),
    ).length,
    [entries, preset, sourceLanguage],
  );

  function setField<K extends keyof GlossaryDraft>(field: K, value: GlossaryDraft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function beginEdit(entry: GlossaryRecord) {
    setDraft(existingGlossaryDraft(entry));
    setEditingId(entry.id);
    setError("");
    setNotice("");
  }

  function cancelEdit() {
    setDraft(newGlossaryDraft(preset, sourceLanguage));
    setEditingId(null);
    setError("");
  }

  function togglePreset(value: ProductPreset, selected: boolean) {
    setDraft((current) => ({
      ...current,
      presets: selected
        ? [...new Set([...current.presets, value])]
        : current.presets.filter((presetValue) => presetValue !== value),
    }));
  }

  async function saveEntry() {
    if (!draft.term.trim()) return;
    if (!draft.globalScope && draft.presets.length === 0) {
      setError(copy.choosePreset);
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const current = entries.find((entry) => entry.id === editingId);
      if (editingId && !current) throw new Error(copy.updateFailed);
      const payload = glossaryPayload(draft, current);
      const response = await fetch(
        current
          ? `${apiUrl}/api/v1/glossary/${encodeURIComponent(current.id)}`
          : `${apiUrl}/api/v1/glossary`,
        {
          method: current ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) throw new Error(await response.text());
      setDraft(newGlossaryDraft(preset, sourceLanguage));
      setEditingId(null);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.saveFailed);
    } finally {
      setBusy(false);
    }
  }

  async function updateEntry(entry: GlossaryRecord, enabled: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${apiUrl}/api/v1/glossary/${entry.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          term: entry.term,
          aliases: entry.aliases,
          translations: entry.translations,
          category: entry.category,
          presets: entry.presets,
          boost: entry.boost,
          enabled,
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.updateFailed);
    } finally {
      setBusy(false);
    }
  }

  async function removeEntry(entry: GlossaryRecord) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${apiUrl}/api/v1/glossary/${entry.id}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.deleteFailed);
    } finally {
      setBusy(false);
    }
  }

  async function importChurchPreset() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${apiUrl}/api/v1/glossary/presets/church`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await response.text());
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.churchFailed);
    } finally {
      setBusy(false);
    }
  }

  async function exportGlossary(format: TransferFormat) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${apiUrl}/api/v1/glossary/export?format=${format}`);
      if (!response.ok) throw new Error(await response.text());
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = downloadFilename(response, `langtextflow-glossary.${format}`);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setNotice(`${format.toUpperCase()} ${copy.exported}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.exportFailed);
    } finally {
      setBusy(false);
    }
  }

  async function importGlossaryFile(file: File) {
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension !== "json" && extension !== "csv") {
      throw new Error(copy.importType);
    }
    const content = await file.text();
    const response = await fetch(`${apiUrl}/api/v1/glossary/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        format: extension,
        content,
        conflict_policy: importPolicy,
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()) as GlossaryImportResult;
  }

  async function handleImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    input.value = "";
    if (!file) return;

    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await importGlossaryFile(file);
      await refresh();
      setNotice(
        `${copy.importDone} · ${copy.created} ${result.created} · ${copy.updated} ${result.updated} · ${copy.skipped} ${result.skipped}`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.importFailed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="glossary-manager" data-testid="glossary-manager">
      <div className="glossary-heading">
        <div>
          <strong>{copy.title}</strong>
          <small>{copy.currentPrefix} {preset} · {applicableCount}{copy.currentSuffix}</small>
        </div>
        {preset === "church" && (
          <button
            type="button"
            className="secondary-button"
            onClick={importChurchPreset}
            disabled={disabled || busy}
          >
            {copy.importChurch}
          </button>
        )}
      </div>

      <div className="glossary-transfer">
        <div className="glossary-transfer-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={() => importInputRef.current?.click()}
            disabled={disabled || busy}
          >
            {copy.importJsonCsv}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => exportGlossary("json")}
            disabled={busy}
          >
            {copy.exportJson}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => exportGlossary("csv")}
            disabled={busy}
          >
            {copy.exportCsv}
          </button>
        </div>
        <label className="glossary-transfer-policy">
          {copy.conflict}
          <select
            value={importPolicy}
            onChange={(event) => setImportPolicy(event.target.value as ImportConflictPolicy)}
            disabled={disabled || busy}
          >
            <option value="upsert">{copy.upsert}</option>
            <option value="skip">{copy.skip}</option>
          </select>
        </label>
        <input
          ref={importInputRef}
          className="glossary-file-input"
          type="file"
          accept=".json,.csv,application/json,text/csv"
          onChange={handleImportFile}
          disabled={disabled || busy}
        />
        <small>{copy.validationHelp}</small>
      </div>

      <div className="glossary-form" data-testid="glossary-editor">
        <label>
          {copy.sourceLanguage}
          <select
            data-testid="glossary-source-language"
            value={draft.sourceLanguage}
            onChange={(event) => setField("sourceLanguage", event.target.value)}
            disabled={disabled || busy}
          >
            {["ko", "en", "ja", "zh", "es", "fr", "de", "it", "pt", "ru"].map(
              (code) => <option key={code} value={code}>{code.toUpperCase()}</option>,
            )}
          </select>
        </label>
        <label>
          {copy.termPlaceholder}
          <input
            data-testid="glossary-term"
            value={draft.term}
            onChange={(event) => setField("term", event.target.value)}
            disabled={disabled || busy}
            maxLength={160}
          />
        </label>
        <label>
          {copy.aliases}
          <input
            data-testid="glossary-aliases"
            value={draft.aliases}
            onChange={(event) => setField("aliases", event.target.value)}
            placeholder={copy.aliasesPlaceholder}
            disabled={disabled || busy}
          />
        </label>
        <div className="glossary-form-row glossary-language-fields">
          <label>
            {copy.korean}
            <input
              data-testid="glossary-translation-ko"
              value={draft.ko}
              onChange={(event) => setField("ko", event.target.value)}
              disabled={disabled || busy}
            />
          </label>
          <label>
            {copy.english}
            <input
              data-testid="glossary-translation-en"
              value={draft.en}
              onChange={(event) => setField("en", event.target.value)}
              disabled={disabled || busy}
            />
          </label>
          <label>
            {copy.japanese}
            <input
              data-testid="glossary-translation-ja"
              value={draft.ja}
              onChange={(event) => setField("ja", event.target.value)}
              disabled={disabled || busy}
            />
          </label>
        </div>
        <div className="glossary-form-row">
          <label>
            {copy.categoryPlaceholder}
            <input
              data-testid="glossary-category"
              value={draft.category}
              onChange={(event) => setField("category", event.target.value)}
              disabled={disabled || busy}
            />
          </label>
          <label>
            {copy.boostLabel}
            <input
              data-testid="glossary-boost"
              type="number" min="0" max="20" step="0.1"
              value={draft.boost}
              onChange={(event) => setField("boost", Number(event.target.value))}
              disabled={disabled || busy}
            />
          </label>
        </div>
        <label className="scope-toggle">
          <input
            type="checkbox"
            checked={draft.globalScope}
            onChange={(event) => setField("globalScope", event.target.checked)}
            disabled={disabled || busy}
          />
          {copy.globalScope}
        </label>
        {!draft.globalScope && (
          <fieldset className="glossary-preset-scope" disabled={disabled || busy}>
            <legend>{copy.presetScope}</legend>
            {GLOSSARY_PRESETS.map((option) => (
              <label key={option} className="scope-toggle">
                <input
                  type="checkbox"
                  checked={draft.presets.includes(option)}
                  onChange={(event) => togglePreset(option, event.target.checked)}
                />
                {option}
              </label>
            ))}
          </fieldset>
        )}
        <div className="glossary-form-actions">
          <button
            data-testid="glossary-save"
            type="button"
            className="secondary-button glossary-add"
            onClick={() => void saveEntry()}
            disabled={
              disabled || busy || !draft.term.trim()
              || (!draft.globalScope && draft.presets.length === 0)
            }
          >
            {editingId ? copy.save : copy.add}
          </button>
          {editingId && (
            <button type="button" className="secondary-button" onClick={cancelEdit}>
              {copy.cancel}
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="glossary-notice">{notice}</div>}

      <div className="glossary-list">
        {entries.length === 0 && <div className="glossary-empty">{copy.empty}</div>}
        {entries.map((entry) => (
          <article className={`glossary-item ${entry.enabled ? "" : "disabled"}`} key={entry.id}>
            <div className="glossary-copy">
              <div>
                <strong>{entry.term}</strong> <small>({entry.source_language ?? "ko"})</small>
                <span>{entry.category}</span>
              </div>
              <small>
                {entry.aliases.length ? `${copy.aliases}: ${entry.aliases.join(", ")}` : copy.noAliases}
                {entry.translations.ko ? ` · KO: ${entry.translations.ko}` : ""}
                {entry.translations.en ? ` · EN: ${entry.translations.en}` : ""}
                {entry.translations.ja ? ` · JA: ${entry.translations.ja}` : ""}
                {targetLanguage !== "en" && targetLanguage !== "ja" && entry.translations[targetLanguage]
                  ? ` · ${targetLanguage.toUpperCase()}: ${entry.translations[targetLanguage]}` : ""}
              </small>
              <small>
                {copy.applies}: {entry.presets.length ? entry.presets.join(", ") : copy.allPresets}
              </small>
            </div>
            <div className="glossary-actions">
              <button
                type="button"
                data-testid="glossary-edit"
                className="secondary-button"
                onClick={() => beginEdit(entry)}
                disabled={disabled || busy}
              >
                {copy.edit}
              </button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => updateEntry(entry, !entry.enabled)}
                disabled={disabled || busy}
              >
                {entry.enabled ? copy.disable : copy.enable}
              </button>
              <button
                type="button"
                className="danger-quiet"
                onClick={() => removeEntry(entry)}
                disabled={disabled || busy}
              >
                {copy.delete}
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
