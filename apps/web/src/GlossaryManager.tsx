import { useCallback, useEffect, useMemo, useState } from "react";
import type { GlossaryEntry, GlossaryRecord, ProductPreset } from "./types";

interface GlossaryManagerProps {
  apiUrl: string;
  preset: ProductPreset;
  targetLanguage: string;
  disabled: boolean;
}

function splitAliases(value: string) {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function GlossaryManager({
  apiUrl,
  preset,
  targetLanguage,
  disabled,
}: GlossaryManagerProps) {
  const [entries, setEntries] = useState<GlossaryRecord[]>([]);
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [translation, setTranslation] = useState("");
  const [category, setCategory] = useState("general");
  const [globalScope, setGlobalScope] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
