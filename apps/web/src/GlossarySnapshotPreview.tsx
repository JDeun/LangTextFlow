import type { Locale } from "./locales";
import type { ResolvedGlossarySnapshot } from "./types";
import { GLOSSARY_COPY } from "./glossaryCopy";
import { GLOSSARY_SNAPSHOT_COPY } from "./glossarySnapshotCopy";

export function GlossarySnapshotPreview({ locale, snapshot, loading, failed, disabled, onPreview }: {
  locale: Locale; snapshot: ResolvedGlossarySnapshot | null; loading: boolean; failed: boolean;
  disabled: boolean; onPreview: () => void;
}) {
  const copy = GLOSSARY_SNAPSHOT_COPY[locale];
  return <section data-testid="glossary-snapshot-preview">
    <strong>{copy.title}</strong>
    <p><small>{copy.help}</small></p>
    <button type="button" className="secondary-button" disabled={disabled || loading}
      data-testid="glossary-snapshot-resolve" onClick={onPreview}>{loading ? copy.loading : copy.preview}</button>
    {failed ? <p role="alert">{copy.failed}</p> : !snapshot && <p><small>{copy.pending}</small></p>}
    {snapshot && <details open data-testid="glossary-snapshot-result">
      <summary>{snapshot.entries.length} {copy.count}</summary>
      {snapshot.entries.length === 0 && <p>{copy.empty}</p>}
      <div role="region" aria-label={copy.title} tabIndex={0} style={{ maxHeight: "20rem", overflowY: "auto" }}>
      {snapshot.entries.map(entry => <article className="glossary-item" key={`${entry.source_language}:${entry.term}`}>
        <div className="glossary-copy"><strong>{entry.term}</strong>
          {Object.entries(entry.translations).map(([language, text]) => <div key={language}>{language}: {text}</div>)}
          {entry.aliases.length > 0 && <small>{copy.aliases}: {entry.aliases.join(", ")}</small>}
          <small>{entry.source_language} · {entry.category} · {GLOSSARY_COPY[locale].boostLabel}: {entry.boost}</small>
        </div>
      </article>)}
      </div>
    </details>}
  </section>;
}
