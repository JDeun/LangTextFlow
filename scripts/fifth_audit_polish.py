from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# --- Onboarding preflight request ordering ---
onboarding = ROOT / "apps/web/src/OnboardingWizard.tsx"
replace_once(
    onboarding,
    'import { useCallback, useEffect, useState } from "react";',
    'import { useCallback, useEffect, useRef, useState } from "react";',
)
replace_once(
    onboarding,
    '  const [microphoneDetail, setMicrophoneDetail] = useState(copy.micInitialDetail);\n\n  const loadPreflight = useCallback(async () => {\n    setLoading(true);',
    '  const [microphoneDetail, setMicrophoneDetail] = useState(copy.micInitialDetail);\n  const preflightRequestRef = useRef(0);\n\n  const loadPreflight = useCallback(async () => {\n    const requestId = ++preflightRequestRef.current;\n    setLoading(true);',
)
replace_once(
    onboarding,
    '      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n      if (!response.ok) throw new Error(`${copy.systemHttp} ${response.status}`);\n      setReport((await response.json()) as SystemPreflight);\n    } catch (reason) {\n      setError(reason instanceof Error ? reason.message : copy.systemFailed);\n    } finally {\n      setLoading(false);\n    }\n  }, [engine, translationModel, translationProvider]);',
    '      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n      if (!response.ok) throw new Error(`${copy.systemHttp} ${response.status}`);\n      const nextReport = (await response.json()) as SystemPreflight;\n      if (requestId === preflightRequestRef.current) setReport(nextReport);\n    } catch (reason) {\n      if (requestId === preflightRequestRef.current) {\n        setError(reason instanceof Error ? reason.message : copy.systemFailed);\n      }\n    } finally {\n      if (requestId === preflightRequestRef.current) setLoading(false);\n    }\n  }, [copy.systemFailed, copy.systemHttp, engine, translationModel, translationProvider]);',
)
replace_once(
    onboarding,
    '  useEffect(() => {\n    if (open) setStep(0);\n  }, [open]);',
    '  useEffect(() => {\n    if (open) {\n      setStep(0);\n    } else {\n      preflightRequestRef.current += 1;\n      setLoading(false);\n    }\n  }, [open]);',
)

# --- Floating preflight request ordering ---
preflight = ROOT / "apps/web/src/PreflightPanel.tsx"
replace_once(
    preflight,
    'import { useCallback, useEffect, useMemo, useState } from "react";',
    'import { useCallback, useEffect, useMemo, useRef, useState } from "react";',
)
replace_once(
    preflight,
    '  const [microphoneDetail, setMicrophoneDetail] = useState(copy.initial);\n\n  const load = useCallback(async () => {\n    setLoading(true);',
    '  const [microphoneDetail, setMicrophoneDetail] = useState(copy.initial);\n  const requestIdRef = useRef(0);\n\n  const load = useCallback(async () => {\n    const requestId = ++requestIdRef.current;\n    setLoading(true);',
)
replace_once(
    preflight,
    '      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n      if (!response.ok) throw new Error(`${copy.http} ${response.status}`);\n      setReport((await response.json()) as SystemPreflight);\n    } catch (reason) {\n      setError(reason instanceof Error ? reason.message : copy.failed);\n    } finally {\n      setLoading(false);\n    }',
    '      const response = await fetch(`${API_URL}/api/v1/preflight?${query}`);\n      if (!response.ok) throw new Error(`${copy.http} ${response.status}`);\n      const nextReport = (await response.json()) as SystemPreflight;\n      if (requestId === requestIdRef.current) setReport(nextReport);\n    } catch (reason) {\n      if (requestId === requestIdRef.current) {\n        setError(reason instanceof Error ? reason.message : copy.failed);\n      }\n    } finally {\n      if (requestId === requestIdRef.current) setLoading(false);\n    }',
)
replace_once(
    preflight,
    '  useEffect(() => {\n    const timer = window.setTimeout(() => void load(), 180);\n    return () => window.clearTimeout(timer);\n  }, [load]);',
    '  useEffect(() => {\n    const timer = window.setTimeout(() => void load(), 180);\n    return () => {\n      window.clearTimeout(timer);\n      requestIdRef.current += 1;\n    };\n  }, [load]);',
)

# --- Session history request ordering and handled manual refresh ---
history = ROOT / "apps/web/src/SessionHistory.tsx"
replace_once(
    history,
    'import { useCallback, useEffect, useMemo, useState } from "react";',
    'import { useCallback, useEffect, useMemo, useRef, useState } from "react";',
)
replace_once(
    history,
    '  const [saveBusy, setSaveBusy] = useState(false);\n\n  const refresh = useCallback(async () => {\n    const response = await fetch(`${apiUrl}/api/v1/history?limit=50`);\n    if (!response.ok) throw new Error(copy.loadFailed);\n    setSessions((await response.json()) as SessionRecord[]);\n  }, [apiUrl, copy.loadFailed]);',
    '  const [saveBusy, setSaveBusy] = useState(false);\n  const refreshRequestRef = useRef(0);\n  const detailRequestRef = useRef(0);\n\n  const refresh = useCallback(async () => {\n    const requestId = ++refreshRequestRef.current;\n    const response = await fetch(`${apiUrl}/api/v1/history?limit=50`);\n    if (!response.ok) throw new Error(copy.loadFailed);\n    const nextSessions = (await response.json()) as SessionRecord[];\n    if (requestId === refreshRequestRef.current) setSessions(nextSessions);\n  }, [apiUrl, copy.loadFailed]);',
)
replace_once(
    history,
    '  async function openDetail(session: SessionRecord) {\n    setDetailBusy(true);',
    '  async function openDetail(session: SessionRecord) {\n    const requestId = ++detailRequestRef.current;\n    setDetailBusy(true);',
)
replace_once(
    history,
    '      const detail = (await detailResponse.json()) as SessionDetail;\n      const transcript = (await captionsResponse.json()) as TranscriptEvent[];\n      const languages = sessionLanguages(detail);',
    '      const detail = (await detailResponse.json()) as SessionDetail;\n      const transcript = (await captionsResponse.json()) as TranscriptEvent[];\n      if (requestId !== detailRequestRef.current) return;\n      const languages = sessionLanguages(detail);',
)
replace_once(
    history,
    '    } catch (reason) {\n      setError(reason instanceof Error ? reason.message : copy.detailFailed);\n    } finally {\n      setDetailBusy(false);\n    }\n  }\n\n  function closeDetail() {\n    setSelected(null);',
    '    } catch (reason) {\n      if (requestId === detailRequestRef.current) {\n        setError(reason instanceof Error ? reason.message : copy.detailFailed);\n      }\n    } finally {\n      if (requestId === detailRequestRef.current) setDetailBusy(false);\n    }\n  }\n\n  function closeDetail() {\n    detailRequestRef.current += 1;\n    setDetailBusy(false);\n    setSelected(null);',
)
replace_once(
    history,
    '        <button className="secondary-button" onClick={() => refresh()}>\n          {copy.refresh}',
    '        <button\n          className="secondary-button"\n          onClick={() => void refresh().catch((reason: Error) => setError(reason.message))}\n        >\n          {copy.refresh}',
)

# --- Product visual convergence ---
shell = ROOT / "apps/web/src/productShell.css"
shell_text = shell.read_text(encoding="utf-8")
marker = "/* Fifth-audit component convergence */"
if marker not in shell_text:
    shell_text += r'''

/* Fifth-audit component convergence */
.preview-toolbar label {
  display: flex;
  align-items: center;
  gap: 8px;
  width: auto;
  margin: 0;
  color: #a9b2ac;
}
.preview-toolbar label select {
  width: auto;
  min-width: 120px;
  margin: 0;
}

.target-language-heading > span { color: var(--ltf-ink-soft); }
.target-language-heading > small { color: var(--ltf-muted); }
.target-language-source {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
}
.target-language-source b { color: var(--ltf-ink); }
.target-language-source span { color: var(--ltf-ink-soft); }
.target-language-source i {
  background: #e8ece9;
  color: #68766d;
}
.target-language-chips button {
  border-color: var(--ltf-line);
  background: var(--ltf-surface);
  color: var(--ltf-muted);
}
.target-language-chips button:hover:not(:disabled) {
  border-color: #b8c2ba;
  background: #f5f7f5;
  color: var(--ltf-ink);
}
.target-language-chips button.active {
  border-color: #9fb0a4;
  background: #e9f0eb;
  color: #355443;
}
.target-language-chips b { color: currentColor; }
.target-language-summary span { color: var(--ltf-muted); }
.target-language-summary small { color: var(--ltf-warning); }

.warning-box {
  border-color: #ead8aa;
  background: var(--ltf-warning-soft);
  color: #805719;
}
.history-heading small,
.history-search-row span,
.history-copy small,
.history-detail-header small,
.history-match-count,
.history-transcript-time,
.history-transcript-state { color: var(--ltf-muted); }
.history-item {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
}
.history-item.selected {
  border-color: #a8b7ac;
  background: #edf3ef;
}
.history-live {
  background: var(--ltf-success-soft);
  color: var(--ltf-success);
}
.history-interrupted {
  background: var(--ltf-warning-soft);
  color: var(--ltf-warning);
}
.history-note-preview { color: var(--ltf-muted) !important; }
.history-actions .history-open {
  border-color: #b9c5bc;
  color: #3e5948;
}
.history-detail { border-top-color: var(--ltf-line); }
.history-detail-meta span,
.history-metadata-editor,
.history-context,
.history-transcript-segment {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
  color: var(--ltf-ink-soft);
}
.history-detail-meta span { color: var(--ltf-muted); }
.history-detail-meta strong,
.history-context p,
.history-transcript-copy { color: var(--ltf-ink); }
.history-speaker { color: #557563; }
.history-source-text { color: var(--ltf-muted); }

.preflight-panel {
  border-color: var(--ltf-line-strong);
  background: rgba(255, 255, 255, .98);
  color: var(--ltf-ink);
  box-shadow: 0 18px 54px rgba(24, 32, 27, .14);
}
.preflight-pill {
  border-color: var(--ltf-line-strong);
  background: var(--ltf-surface);
  color: var(--ltf-ink-soft);
  box-shadow: 0 8px 26px rgba(24, 32, 27, .1);
}
.preflight-pill.ready {
  border-color: #b9d9c6;
  background: var(--ltf-success-soft);
  color: var(--ltf-success);
}
.preflight-pill.attention {
  border-color: #ead8aa;
  background: var(--ltf-warning-soft);
  color: var(--ltf-warning);
}
.preflight-titlebar small { color: var(--ltf-muted); }
.preflight-titlebar strong { color: var(--ltf-ink); }
.preflight-close {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
  color: var(--ltf-muted);
}
.preflight-system span {
  background: var(--ltf-surface-subtle);
  color: var(--ltf-muted);
}
.preflight-recommendation {
  border-color: #c7d9cc;
  background: #f0f6f2;
}
.preflight-recommendation small { color: #648171; }
.preflight-recommendation strong { color: #345240; }
.preflight-recommendation ul { color: #65776b; }
.preflight-recommendation button {
  border-color: #aebfb3;
  background: #e4eee7;
  color: #355443;
}
.preflight-check {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
}
.preflight-check > i {
  background: #e6eae7;
  color: #69756d;
}
.preflight-check strong { color: var(--ltf-ink); }
.preflight-check span { color: var(--ltf-muted); }
.preflight-check.ready { border-color: #c8dfd0; }
.preflight-check.ready > i {
  background: var(--ltf-success-soft);
  color: var(--ltf-success);
}
.preflight-check.warning { border-color: #ead8aa; }
.preflight-check.warning > i {
  background: var(--ltf-warning-soft);
  color: var(--ltf-warning);
}
.preflight-check.missing,
.preflight-check.error {
  border-color: #efc9cc;
  background: var(--ltf-danger-soft);
}
.preflight-check.missing > i,
.preflight-check.error > i {
  background: #f7dfe1;
  color: var(--ltf-danger);
}
.preflight-actions button {
  border-color: var(--ltf-line-strong);
  background: var(--ltf-surface);
  color: var(--ltf-ink-soft);
}
.preflight-error {
  border-color: #efc9cc;
  background: var(--ltf-danger-soft);
  color: #9f3941;
}
'''
shell.write_text(shell_text.rstrip() + "\n", encoding="utf-8")

# --- Capture onboarding and guard layout in E2E ---
e2e = ROOT / "apps/web/tests/e2e-smoke.mjs"
e2e_text = e2e.read_text(encoding="utf-8")
old = '''await setViewport(1440, 1000, false);\nawait navigate("http://127.0.0.1:5173/");\nawait evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); true`);\n'''
new = '''await setViewport(1440, 1000, false);\nawait navigate("http://127.0.0.1:5173/");\nawait waitFor(\n  () => evaluate(`document.querySelector('.onboarding-dialog') !== null`),\n  "onboarding dialog",\n);\nawait screenshot("onboarding-en");\nawait evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); true`);\n'''
if old not in e2e_text:
    raise SystemExit("missing onboarding E2E anchor")
e2e.write_text(e2e_text.replace(old, new, 1), encoding="utf-8")

print("fifth audit stability polish applied")
