import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GLOSSARY_COPY } from "./glossaryCopy";
import { useI18n } from "./i18n";
import type { GlossaryEntry, GlossaryRecord, ProductPreset } from "./types";

interface GlossaryManagerProps {
  apiUrl: string;
  preset: ProductPreset;
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

function splitAliases(value: string) {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function downloadFilename(response: Response, fallback: string) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

export function GlossaryManager({
  apiUrl,
  preset,
  targetLanguage,
  disabled,
}: GlossaryManagerProps) {
  const { locale } = useI18n();
  const copy = GLOSSARY_COPY[locale];
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [entries, setEntries] = useState<GlossaryRecord[]>([]);
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [translation, setTranslation] = useState("");
  const [category, setCategory] = useState("general");
  const [globalScope, setGlobalScope] = useState(false);
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
      (entry) => entry.enabled && (!entry.presets.length || entry.presets.includes(preset)),
    ).length,
    [entries, preset],
  );

  async function addEntry() {
    if (!term.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload: GlossaryEntry = {
        term: term.trim(),
        aliases: splitAliases(aliases),
        translations: translation.trim() ? { [targetLanguage]: translation.trim() } : {},
        category: category.trim() || "general",
        presets: globalScope ? [] : [preset],
        boost: 1.5,
        enabled: true,
      };
      const response = await fetch(`${apiUrl}/api/v1/glossary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await response.text());
      setTerm("");
      setAliases("");
      setTranslation("");
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
    <section className="glossary-manager">
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

      <div className="glossary-form">
        <input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder={copy.termPlaceholder}
          disabled={disabled || busy}
        />
        <input
          value={aliases}
          onChange={(event) => setAliases(event.target.value)}
          placeholder={copy.aliasesPlaceholder}
          disabled={disabled || busy}
        />
        <div className="glossary-form-row">
          <input
            value={translation}
            onChange={(event) => setTranslation(event.target.value)}
            placeholder={`${targetLanguage.toUpperCase()} ${copy.translationOptional}`}
            disabled={disabled || busy}
          />
          <input
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            placeholder={copy.categoryPlaceholder}
            disabled={disabled || busy}
          />
        </div>
        <label className="scope-toggle">
          <input
            type="checkbox"
            checked={globalScope}
            onChange={(event) => setGlobalScope(event.target.checked)}
            disabled={disabled || busy}
          />
          {copy.globalScope}
        </label>
        <button
          type="button"
          className="secondary-button glossary-add"
          onClick={addEntry}
          disabled={disabled || busy || !term.trim()}
        >
          {copy.add}
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="glossary-notice">{notice}</div>}

      <div className="glossary-list">
        {entries.length === 0 && <div className="glossary-empty">{copy.empty}</div>}
        {entries.map((entry) => (
          <article className={`glossary-item ${entry.enabled ? "" : "disabled"}`} key={entry.id}>
            <div className="glossary-copy">
              <div>
                <strong>{entry.term}</strong>
                <span>{entry.category}</span>
              </div>
              <small>
                {entry.aliases.length ? `${copy.aliases}: ${entry.aliases.join(", ")}` : copy.noAliases}
                {entry.translations[targetLanguage]
                  ? ` · ${targetLanguage.toUpperCase()}: ${entry.translations[targetLanguage]}`
                  : ""}
              </small>
              <small>
                {copy.applies}: {entry.presets.length ? entry.presets.join(", ") : copy.allPresets}
              </small>
            </div>
            <div className="glossary-actions">
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
