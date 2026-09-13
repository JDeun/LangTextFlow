import { useRef, useState } from "react";
import type { ChangeEvent } from "react";
import "./contextDocuments.css";
import { useI18n } from "./i18n";
import type { ReferenceDocument } from "./types";
import { UTILITY_COPY } from "./utilityCopy";

const MAX_DOCUMENTS = 4;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set(["txt", "md", "markdown", "pdf", "docx"]);

interface ContextDocumentManagerProps {
  value: ReferenceDocument[];
  disabled?: boolean;
  onChange: (documents: ReferenceDocument[]) => void;
}

function extension(filename: string) {
  const index = filename.lastIndexOf(".");
  return index >= 0 ? filename.slice(index + 1).toLowerCase() : "";
}

function sizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function arrayBufferToBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length));
    binary += String.fromCharCode(...chunk);
  }
  return window.btoa(binary);
}

export function ContextDocumentManager({
  value,
  disabled = false,
  onChange,
}: ContextDocumentManagerProps) {
  const { locale } = useI18n();
  const copy = UTILITY_COPY[locale].context;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function toReferenceDocument(file: File): Promise<ReferenceDocument> {
    if (!ALLOWED_EXTENSIONS.has(extension(file.name))) {
      throw new Error(`${file.name}: ${copy.badType}`);
    }
    if (file.size <= 0) throw new Error(`${file.name}: ${copy.empty}`);
    if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name}: ${copy.tooLarge}`);

    return {
      filename: file.name,
      media_type: file.type || "application/octet-stream",
      size_bytes: file.size,
      content_base64: arrayBufferToBase64(await file.arrayBuffer()),
    };
  }

  async function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;

    setBusy(true);
    setError("");
    try {
      const additions = await Promise.all(files.map(toReferenceDocument));
      const next = [...value];
      for (const document of additions) {
        const existing = next.findIndex((item) => item.filename === document.filename);
        if (existing >= 0) next[existing] = document;
        else next.push(document);
      }
      if (next.length > MAX_DOCUMENTS) {
        throw new Error(`${copy.maxDocs} ${MAX_DOCUMENTS}.`);
      }
      onChange(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : copy.readFailed);
    } finally {
      setBusy(false);
    }
  }

  function remove(filename: string) {
    onChange(value.filter((item) => item.filename !== filename));
  }

  return (
    <section className="context-documents">
      <div className="context-documents-heading">
        <div>
          <strong>{copy.title}</strong>
          <small>{copy.help}</small>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled || busy || value.length >= MAX_DOCUMENTS}
        >
          {busy ? copy.reading : copy.add}
        </button>
      </div>
      <input
        ref={inputRef}
        className="context-document-input"
        type="file"
        accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        multiple
        onChange={addFiles}
        disabled={disabled || busy}
      />
      <small className="context-document-note">{copy.note}</small>
      {error && <div className="error-box">{error}</div>}
      {value.length > 0 && (
        <div className="context-document-list">
          {value.map((document) => (
            <article key={document.filename}>
              <div>
                <strong>{document.filename}</strong>
                <small>
                  {sizeLabel(document.size_bytes)}
                  {typeof document.character_count === "number" && document.character_count > 0
                    ? ` · ${document.character_count.toLocaleString()} ${copy.extracted}${document.truncated ? ` · ${copy.truncated}` : ""}`
                    : ` · ${copy.localExtract}`}
                </small>
              </div>
              <button
                className="danger-quiet"
                type="button"
                onClick={() => remove(document.filename)}
                disabled={disabled}
              >
                {copy.remove}
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
