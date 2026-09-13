from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

styles_path = ROOT / "apps/web/src/styles.css"
styles = styles_path.read_text(encoding="utf-8")
escaped_marker = r"\n\n/* Progressive disclosure for operator setup */"
if escaped_marker in styles:
    index = styles.index(escaped_marker)
    suffix = styles[index:].replace(r"\n", "\n").replace(r"\r", "\r")
    styles = styles[:index].rstrip() + "\n\n" + suffix.lstrip()
styles_path.write_text(styles.rstrip() + "\n", encoding="utf-8")

product_shell = r'''/*
 * LangTextFlow product shell
 * Clean communication SaaS for everyday operation, with technical controls
 * visually demoted into progressive-disclosure operator layers.
 */

:root {
  --ltf-bg: #f5f6f4;
  --ltf-surface: #ffffff;
  --ltf-surface-subtle: #f8f9f7;
  --ltf-surface-raised: #fbfcfa;
  --ltf-ink: #18201b;
  --ltf-ink-soft: #465049;
  --ltf-muted: #737d76;
  --ltf-line: #e1e5e1;
  --ltf-line-strong: #cfd5d0;
  --ltf-accent: #202a24;
  --ltf-accent-soft: #edf1ed;
  --ltf-success: #237a4b;
  --ltf-success-soft: #eaf6ee;
  --ltf-danger: #b84a51;
  --ltf-danger-soft: #fff0f1;
  --ltf-warning: #9b6b22;
  --ltf-warning-soft: #fff8e9;
  --ltf-radius-lg: 18px;
  --ltf-radius-md: 12px;
  --ltf-shadow: 0 1px 2px rgba(24, 32, 27, .04), 0 12px 34px rgba(24, 32, 27, .055);
  --surface-0: var(--ltf-bg);
  --surface-1: var(--ltf-surface);
  --surface-2: var(--ltf-surface-subtle);
  --line: var(--ltf-line);
  --line-strong: var(--ltf-line-strong);
  --text: var(--ltf-ink);
  --muted: var(--ltf-muted);
  --accent: #61786a;
  --accent-soft: rgba(97, 120, 106, .1);
  --success: var(--ltf-success);
  --danger: var(--ltf-danger);
  color-scheme: light;
}

html { background: var(--ltf-bg); }
body {
  color: var(--ltf-ink);
  background: var(--ltf-bg);
}

.app-shell {
  max-width: 1540px;
  padding: 20px 28px 52px;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  min-height: 70px;
  margin: 0 -10px 22px;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(225, 229, 225, .9);
  background: rgba(245, 246, 244, .92);
  backdrop-filter: blur(18px) saturate(135%);
}

.brand {
  color: var(--ltf-ink);
  font-size: 21px;
  font-weight: 720;
  letter-spacing: -.025em;
}

.subtitle { color: var(--ltf-muted); }

.product-nav {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--ltf-line);
  border-radius: 12px;
  background: rgba(255, 255, 255, .72);
}

.product-nav button {
  width: auto;
  min-height: 34px;
  margin: 0;
  padding: 7px 11px;
  border-radius: 8px;
  color: var(--ltf-muted);
  background: transparent;
  font-size: 12px;
  font-weight: 600;
}

.product-nav button:hover {
  color: var(--ltf-ink);
  background: var(--ltf-accent-soft);
}

.topbar-actions { align-items: center; gap: 8px; }
.locale-switcher { margin: 0; }
.locale-switcher select,
.locale-switcher.compact select {
  min-width: 104px;
  padding: 8px 30px 8px 10px;
  border-color: var(--ltf-line);
  background: var(--ltf-surface);
  color: var(--ltf-ink-soft);
  font-size: 12px;
}

.connection {
  color: var(--ltf-muted);
  font-weight: 560;
}

.dot {
  width: 7px;
  height: 7px;
  background: #c96c72;
  box-shadow: none;
}

.connection.online .dot {
  background: #3c9564;
  box-shadow: none;
}

.workspace {
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 20px;
}

.panel {
  border: 1px solid var(--ltf-line);
  border-radius: var(--ltf-radius-lg);
  background: var(--ltf-surface);
  box-shadow: var(--ltf-shadow);
}

.control-panel {
  position: sticky;
  top: 92px;
  max-height: calc(100vh - 112px);
  padding: 20px;
  overflow: auto;
  scrollbar-width: thin;
  scrollbar-color: #c8cec9 transparent;
}

.section-heading {
  color: var(--ltf-ink);
  font-size: 14px;
  font-weight: 680;
}

.beta {
  color: #607067;
  background: #edf1ee;
}

label {
  color: var(--ltf-ink-soft);
  font-size: 12px;
  font-weight: 560;
}

label small {
  color: var(--ltf-muted);
  font-weight: 450;
}

select,
input,
textarea {
  border: 1px solid var(--ltf-line-strong);
  border-radius: 10px;
  background: var(--ltf-surface);
  color: var(--ltf-ink);
  box-shadow: 0 1px 1px rgba(24, 32, 27, .025);
}

select:hover,
input:hover,
textarea:hover { border-color: #b8c0ba; }

select:focus,
input:focus,
textarea:focus {
  border-color: #71877a;
  box-shadow: 0 0 0 3px rgba(97, 120, 106, .12);
}

button {
  transition: background-color .14s ease, border-color .14s ease, color .14s ease, transform .14s ease;
}

button:active:not(:disabled) { transform: translateY(1px); }

.secondary-button {
  border: 1px solid var(--ltf-line);
  background: var(--ltf-surface);
  color: var(--ltf-ink-soft);
  box-shadow: 0 1px 1px rgba(24, 32, 27, .025);
}

.secondary-button:hover:not(:disabled) {
  border-color: var(--ltf-line-strong);
  background: var(--ltf-surface-subtle);
  color: var(--ltf-ink);
}

.start-button,
.stop-button {
  position: sticky;
  bottom: 0;
  z-index: 8;
  min-height: 48px;
  border: 1px solid transparent;
  box-shadow: 0 -14px 24px rgba(255, 255, 255, .92);
}

.start-button {
  background: #172019;
  color: #fff;
}

.start-button:hover:not(:disabled) { background: #253128; }

.stop-button {
  border-color: #f1cdd0;
  background: var(--ltf-danger-soft);
  color: var(--ltf-danger);
}

.device-block,
.glossary-manager,
.context-documents {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
}

.error-box {
  border-color: #efc9cc;
  background: var(--ltf-danger-soft);
  color: #9f3941;
}

.warning-box,
.network-warning {
  border-color: #ead8aa;
  background: var(--ltf-warning-soft);
  color: #805719;
}

.settings-group {
  margin: 12px 0;
  border: 1px solid var(--ltf-line);
  border-radius: 12px;
  background: var(--ltf-surface-subtle);
  overflow: clip;
}

.settings-group > summary {
  min-height: 54px;
  padding: 12px 13px;
}

.settings-group > summary:hover { background: #f2f4f1; }
.settings-group > summary strong { color: var(--ltf-ink); }
.settings-group > summary small { color: var(--ltf-muted); }
.settings-group > summary::after { color: #7b867e; }
.settings-group[open] { background: var(--ltf-surface-raised); }
.settings-group-body { border-top-color: var(--ltf-line); }

.pipeline-card,
.telemetry-card {
  border: 1px solid var(--ltf-line);
  background: var(--ltf-surface-subtle);
  color: var(--ltf-ink);
  box-shadow: none;
}

.pipeline-card span,
.pipeline-card small,
.telemetry-card small { color: var(--ltf-muted); }

.stage-track i { background: #dce1dd; }
.stage-track i.active {
  background: #5f7869;
  box-shadow: none;
}

.telemetry-heading,
.telemetry-input,
.provider-health,
.telemetry-metric {
  border-color: var(--ltf-line) !important;
  background: var(--ltf-surface) !important;
  color: var(--ltf-ink) !important;
}

.telemetry-metric span,
.telemetry-input small,
.provider-health small { color: var(--ltf-muted) !important; }

.main-column { gap: 18px; }

.share-panel {
  border-color: var(--ltf-line);
  background: var(--ltf-surface);
}

.eyebrow,
.share-note { color: var(--ltf-muted); }
.join-code { color: var(--ltf-ink); }

.qr-card {
  border: 1px solid var(--ltf-line);
  background: #fff;
  color: var(--ltf-ink-soft);
  box-shadow: none;
}

.preview {
  border-color: #dfe3df;
  background: #0d1110;
  box-shadow: 0 18px 50px rgba(24, 32, 27, .09);
}

.preview-toolbar {
  border-bottom-color: #27302a;
  background: #151b17;
  color: #a9b2ac;
}

.preview-toolbar label { color: #a9b2ac; }
.preview-toolbar select {
  border-color: #354039;
  background: #1b231e;
  color: #eef2ef;
}

.stage-screen {
  min-height: 390px;
  background: radial-gradient(circle at 50% 115%, #263229 0, #101511 48%, #0a0d0b 100%);
}

.caption { color: #f7faf8; }
.source-caption { color: #9da8a1; }

.transcript { background: var(--ltf-surface); }
.segment {
  border-color: var(--ltf-line);
  background: var(--ltf-surface-subtle);
}
.segment-meta,
.segment-source,
.empty { color: var(--ltf-muted); }
.stage-tag {
  background: #e9edea;
  color: #647168;
}
.stage-tag.committed {
  background: var(--ltf-success-soft);
  color: var(--ltf-success);
}

/* Audience is content-first: controls recede and captions dominate. */
.audience-shell {
  max-width: 940px;
  padding: 30px clamp(18px, 4vw, 42px) 54px;
  color: var(--ltf-ink);
}

.audience-header {
  position: sticky;
  top: 0;
  z-index: 10;
  margin-bottom: 10px;
  padding: 12px 0;
  border-bottom: 1px solid rgba(225, 229, 225, .82);
  background: rgba(245, 246, 244, .92);
  backdrop-filter: blur(16px);
}

.audience-language {
  max-width: 250px;
  margin: 14px 0 18px;
}

.audience-caption-card {
  min-height: 400px;
  padding: clamp(28px, 5vw, 52px);
  border: 1px solid var(--ltf-line);
  border-radius: 22px;
  background: var(--ltf-surface);
  color: var(--ltf-ink);
  box-shadow: var(--ltf-shadow);
}

.audience-caption {
  color: var(--ltf-ink);
  text-shadow: none;
}

.audience-source { color: var(--ltf-muted); }
.audience-transcript { margin-top: 22px; }
.audience-transcript article {
  border-bottom-color: var(--ltf-line);
  color: var(--ltf-ink-soft);
}
.audience-transcript small { color: var(--ltf-muted); }
.audience-error { color: var(--ltf-muted); }

/* Projector / OBS remain dark by design for stage legibility. */
.display-shell { color: #fff; background: #050706; }
.display-shell.obs { background: transparent; }

button:focus-visible,
summary:focus-visible,
select:focus-visible,
input:focus-visible,
textarea:focus-visible {
  outline: 2px solid #61786a;
  outline-offset: 2px;
}

@media (max-width: 1080px) {
  .workspace { grid-template-columns: 330px minmax(0, 1fr); }
}

@media (max-width: 860px) {
  .app-shell { padding: 14px 14px 36px; }
  .topbar { align-items: flex-start; flex-wrap: wrap; margin-bottom: 14px; }
  .workspace { grid-template-columns: 1fr; }
  .control-panel {
    position: static;
    max-height: none;
    order: 2;
  }
  .main-column { order: 1; }
  .stage-screen { min-height: 280px; }
  .start-button,
  .stop-button { box-shadow: 0 -12px 20px rgba(255, 255, 255, .94); }
}

@media (max-width: 560px) {
  .app-shell { padding-inline: 12px; }
  .topbar { padding-inline: 4px; }
  .brand { font-size: 19px; }
  .connection { font-size: 11px; }
  .locale-switcher.compact select { min-width: 90px; }
  .panel { border-radius: 15px; }
  .audience-shell { padding: 16px 14px 36px; }
  .audience-caption-card {
    min-height: 300px;
    padding: 26px 22px;
    border-radius: 18px;
  }
  .audience-header .subtitle { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: .01ms !important;
    animation-duration: .01ms !important;
  }
}
'''
(ROOT / "apps/web/src/productShell.css").write_text(product_shell, encoding="utf-8")

css_hygiene = r'''import { readdir, readFile } from "node:fs/promises";

const src = new URL("../src/", import.meta.url);
const findings = [];

async function scan(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const url = new URL(entry.name + (entry.isDirectory() ? "/" : ""), directory);
    if (entry.isDirectory()) {
      await scan(url);
      continue;
    }
    if (!entry.name.endsWith(".css")) continue;
    const text = await readFile(url, "utf8");
    const name = decodeURIComponent(url.pathname.split("/apps/web/").at(-1));
    if (text.includes("\\n") || text.includes("\\r")) {
      findings.push(`${name}: contains literal escaped newline characters`);
    }
    for (const [label, pattern] of [
      ["CSS expression()", /expression\s*\(/i],
      ["javascript URL", /javascript\s*:/i],
      ["HTML data URL", /data\s*:\s*text\/html/i],
    ]) {
      if (pattern.test(text)) findings.push(`${name}: contains ${label}`);
    }
    const stripped = text
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, "");
    let balance = 0;
    for (const char of stripped) {
      if (char === "{") balance += 1;
      if (char === "}") balance -= 1;
      if (balance < 0) break;
    }
    if (balance !== 0) findings.push(`${name}: unbalanced CSS braces (${balance})`);
  }
}

await scan(src);
if (findings.length) {
  console.error("CSS hygiene gate failed:\n" + findings.join("\n"));
  process.exit(1);
}
console.log("CSS hygiene gate passed.");
'''
(ROOT / "apps/web/scripts/check-css-hygiene.mjs").write_text(css_hygiene, encoding="utf-8")

e2e_path = ROOT / "apps/web/tests/e2e-smoke.mjs"
e2e = e2e_path.read_text(encoding="utf-8")
needle = '''for (const [locale, expected] of localeExpectations) {
  await setLocale(locale);
  await waitFor(
    () => evaluate(`document.querySelector('[data-testid="session-toggle"]')?.textContent?.includes(${JSON.stringify(expected)})`),
    `${locale} primary action`,
  );
  await screenshot(`operator-${locale}`);
}
'''
replacement = needle + '''
const disclosureState = await evaluate(`(() => {
  const groups = Array.from(document.querySelectorAll('.settings-group'));
  return groups.length >= 2 && groups.every((group) => !group.open);
})()`);
if (!disclosureState) throw new Error("advanced operator groups must be collapsed by default");

const disclosureToggle = await evaluate(`(() => {
  const group = document.querySelectorAll('.settings-group')[1];
  if (!group) return false;
  group.open = true;
  const opened = group.open;
  group.open = false;
  return opened && !group.open;
})()`);
if (!disclosureToggle) throw new Error("advanced operator group disclosure is not operable");
'''
if needle not in e2e:
    raise SystemExit("Could not find locale E2E insertion point")
e2e = e2e.replace(needle, replacement, 1)
needle = '''await screenshot("operator-mobile-ko");
await setViewport(1440, 1000, false);
'''
replacement = '''await screenshot("operator-mobile-ko");
const operatorMobileFits = await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`);
if (!operatorMobileFits) throw new Error("operator mobile layout has horizontal overflow");
await setViewport(1440, 1000, false);
'''
if needle not in e2e:
    raise SystemExit("Could not find operator mobile E2E insertion point")
e2e = e2e.replace(needle, replacement, 1)
needle = '''await screenshot("audience-mobile-ko");
await setViewport(1440, 1000, false);
'''
replacement = '''await screenshot("audience-mobile-ko");
const audienceIsolation = await evaluate(`(() => {
  const fits = document.documentElement.scrollWidth <= window.innerWidth + 1;
  const noOperatorControls = !document.querySelector('.control-panel') && !document.querySelector('[data-testid="session-toggle"]');
  return fits && noOperatorControls;
})()`);
if (!audienceIsolation) throw new Error("audience view leaked operator UI or overflows horizontally");
await setViewport(1440, 1000, false);
'''
if needle not in e2e:
    raise SystemExit("Could not find audience mobile E2E insertion point")
e2e = e2e.replace(needle, replacement, 1)
e2e_path.write_text(e2e, encoding="utf-8")

print("fifth audit patch applied")
