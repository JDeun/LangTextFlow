import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    if (!response.ok) throw new Error("용어집을 불러오지 못했습니다.");
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
      setError(reason instanceof Error ? reason.message : "용어를 저장하지 못했습니다.");
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
      setError(reason instanceof Error ? reason.message : "용어 상태를 변경하지 못했습니다.");
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
      setError(reason instanceof Error ? reason.message : "용어를 삭제하지 못했습니다.");
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
      setError(reason instanceof Error ? reason.message : "교회 기본 용어를 가져오지 못했습니다.");
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
      setNotice(`${format.toUpperCase()} 용어집을 내보냈습니다.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "용어집을 내보내지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function importGlossaryFile(file: File) {
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension !== "json" && extension !== "csv") {
      throw new Error("JSON 또는 CSV 파일만 가져올 수 있습니다.");
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
        `가져오기 완료 · 생성 ${result.created} · 갱신 ${result.updated} · 건너뜀 ${result.skipped}`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "용어집을 가져오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="glossary-manager">
      <div className="glossary-heading">
        <div>
          <strong>용어집</strong>
          <small>현재 {preset} 세션에 {applicableCount}개 적용</small>
        </div>
        {preset === "church" && (
          <button
            type="button"
            className="secondary-button"
            onClick={importChurchPreset}
            disabled={disabled || busy}
          >
            교회 기본 용어 가져오기
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
            JSON / CSV 가져오기
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => exportGlossary("json")}
            disabled={busy}
          >
            JSON 내보내기
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => exportGlossary("csv")}
            disabled={busy}
          >
            CSV 내보내기
          </button>
        </div>
        <label className="glossary-transfer-policy">
          중복 용어 처리
          <select
            value={importPolicy}
            onChange={(event) => setImportPolicy(event.target.value as ImportConflictPolicy)}
            disabled={disabled || busy}
          >
            <option value="upsert">기존 항목 업데이트</option>
            <option value="skip">기존 항목 건너뛰기</option>
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
        <small>가져오기 전에 파일 전체를 검증합니다. 업데이트 시 기존 ID는 유지됩니다.</small>
      </div>

      <div className="glossary-form">
        <input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="표준 용어 · 예: 요한복음"
          disabled={disabled || busy}
        />
        <input
          value={aliases}
          onChange={(event) => setAliases(event.target.value)}
          placeholder="오인식/별칭 · 쉼표로 구분"
          disabled={disabled || busy}
        />
        <div className="glossary-form-row">
          <input
            value={translation}
            onChange={(event) => setTranslation(event.target.value)}
            placeholder={`${targetLanguage.toUpperCase()} 번역 · 선택 사항`}
            disabled={disabled || busy}
          />
          <input
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            placeholder="카테고리"
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
          모든 preset에서 사용
        </label>
        <button
          type="button"
          className="secondary-button glossary-add"
          onClick={addEntry}
          disabled={disabled || busy || !term.trim()}
        >
          용어 추가
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="glossary-notice">{notice}</div>}

      <div className="glossary-list">
        {entries.length === 0 && <div className="glossary-empty">저장된 용어가 없습니다.</div>}
        {entries.map((entry) => (
          <article className={`glossary-item ${entry.enabled ? "" : "disabled"}`} key={entry.id}>
            <div className="glossary-copy">
              <div>
                <strong>{entry.term}</strong>
                <span>{entry.category}</span>
              </div>
              <small>
                {entry.aliases.length ? `별칭: ${entry.aliases.join(", ")}` : "별칭 없음"}
                {entry.translations[targetLanguage]
                  ? ` · ${targetLanguage.toUpperCase()}: ${entry.translations[targetLanguage]}`
                  : ""}
              </small>
              <small>
                적용: {entry.presets.length ? entry.presets.join(", ") : "전체 preset"}
              </small>
            </div>
            <div className="glossary-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={() => updateEntry(entry, !entry.enabled)}
                disabled={disabled || busy}
              >
                {entry.enabled ? "끄기" : "켜기"}
              </button>
              <button
                type="button"
                className="danger-quiet"
                onClick={() => removeEntry(entry)}
                disabled={disabled || busy}
              >
                삭제
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
