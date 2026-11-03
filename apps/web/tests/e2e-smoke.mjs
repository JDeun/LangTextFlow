import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, label, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await check()) return;
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}${lastError ? `: ${lastError}` : ""}`);
}

const targetResponse = await fetch(
  `http://127.0.0.1:9222/json/new?${encodeURIComponent("http://127.0.0.1:5173/")}`,
  { method: "PUT" },
);
if (!targetResponse.ok) throw new Error(`CDP target creation failed: ${targetResponse.status}`);
const target = await targetResponse.json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, { once: true });
  socket.addEventListener("error", reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});

function cdp(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "browser evaluation failed");
  }
  return result.result?.value;
}

async function navigate(url) {
  await cdp("Page.navigate", { url });
  await waitFor(() => evaluate("document.readyState === 'complete'"), `page load: ${url}`);
}

async function setViewport(width, height, mobile = false, deviceScaleFactor = 1) {
  await cdp("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor,
    mobile,
  });
  await sleep(100);
}

async function pressEnter() {
  await cdp("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Enter",
    code: "Enter",
    text: "\r",
    unmodifiedText: "\r",
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
  });
  await cdp("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
    nativeVirtualKeyCode: 13,
  });
}

async function setLocale(locale) {
  // Exercise the real React onChange path. Modifying localStorage then
  // immediately reloading races React's locale persistence effect.
  await waitFor(
    () => evaluate(`document.querySelector('[data-testid="locale-select"]') !== null`),
    "locale control ready",
  );
  const dispatched = await evaluate(`(() => {
    const select = document.querySelector('[data-testid="locale-select"]');
    if (!(select instanceof HTMLSelectElement)) return false;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLSelectElement.prototype, 'value',
    )?.set;
    if (!setter) return false;
    setter.call(select, ${JSON.stringify(locale)});
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`);
  if (!dispatched) throw new Error("locale selector cannot dispatch changes");
  await waitFor(
    () => evaluate(`document.documentElement.lang === ${JSON.stringify(locale)}
      && document.querySelector('[data-testid="locale-select"]')?.value === ${JSON.stringify(locale)}
      && localStorage.getItem('langtextflow:locale:v1') === ${JSON.stringify(locale)}`),
    `locale ${locale} UI and persistence`,
  );
}

async function screenshot(name) {
  const result = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await mkdir("artifacts", { recursive: true });
  await writeFile(`artifacts/${name}.png`, Buffer.from(result.data, "base64"));
}

await cdp("Page.enable");
await cdp("Runtime.enable");
await setViewport(1440, 1000, false);
await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('.onboarding-dialog') !== null`),
  "onboarding dialog",
);
await screenshot("onboarding-en");
await evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); true`);

const localeExpectations = [
  ["en", "Start session"],
  ["ko", "세션 시작"],
  ["ja", "セッション開始"],
];
for (const [locale, expected] of localeExpectations) {
  await setLocale(locale);
  await waitFor(
    () => evaluate(`document.querySelector('[data-testid="session-toggle"]')?.textContent?.includes(${JSON.stringify(expected)})`),
    `${locale} primary action`,
  );
  await screenshot(`operator-${locale}`);
}

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

await setLocale("ko");
await setViewport(1440, 1000, false, 2);
const retinaFits = await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`);
if (!retinaFits) throw new Error("desktop high-DPI layout has horizontal overflow");
await screenshot("operator-retina-ko");

const longStringFits = await evaluate(`(() => {
  const targets = [
    document.querySelector('.brand'),
    document.querySelector('.subtitle'),
    document.querySelector('.section-heading span'),
  ].filter(Boolean);
  const originals = targets.map((node) => node.textContent);
  const stress = '초장문현지화레이아웃검증문자열'.repeat(10);
  targets.forEach((node) => { node.textContent = stress; });
  const fits = document.documentElement.scrollWidth <= window.innerWidth + 1;
  targets.forEach((node, index) => { node.textContent = originals[index]; });
  return fits;
})()`);
if (!longStringFits) throw new Error("long-string localization stress caused horizontal overflow");

await setViewport(390, 844, true);
await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('.preflight-pill') !== null && document.querySelector('.preflight-panel') === null`),
  "collapsed mobile preflight",
);
await screenshot("operator-mobile-ko");
const operatorMobileFits = await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`);
if (!operatorMobileFits) throw new Error("operator mobile layout has horizontal overflow");
await setViewport(1440, 1000, false);
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="connection-status"]')?.textContent?.includes("서버 연결됨")`),
  "operator caption socket connection",
);

// Standalone operator glossary deep link: bilingual create/edit/persist and back navigation.
await waitFor(() => evaluate(`document.querySelector('.control-panel [data-testid="translation-provider-select"] option[value="openai-compatible"]') !== null`), "provider metadata for model overrides");
async function selectTranslationProvider(provider) {
  await evaluate(`(() => {
    const select = document.querySelector('.control-panel [data-testid="translation-provider-select"]');
    select.value = ${JSON.stringify(provider)};
    select.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
}
await selectTranslationProvider("ollama");
await waitFor(() => evaluate(`document.querySelector('[data-testid="translation-model-input"]')?.value === ''`), "configured backend model starts without a frontend override");
await evaluate(`(() => {
  const input = document.querySelector('[data-testid="translation-model-input"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'operator-local-model');
  input.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await selectTranslationProvider("openai-compatible");
await waitFor(() => evaluate(`document.querySelector('[data-testid="translation-model-input"]')?.value === ''`), "local override cannot leak into an external provider");
await selectTranslationProvider("ollama");
await waitFor(() => evaluate(`document.querySelector('[data-testid="translation-model-input"]')?.value === 'operator-local-model'`), "local provider retains its own explicit override");
await evaluate(`(() => {
  const input = document.querySelector('[data-testid="translation-model-input"]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
  input.dispatchEvent(new Event('input', { bubbles: true }));
})()`);

const glossaryCreated = await fetch("http://127.0.0.1:8000/api/v1/glossary", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    term: "강연자료 E2E", aliases: ["자료 E2E"],
    translations: { en: "lecture notes" }, category: "lecture",
    presets: ["lecture"], boost: 1.4, enabled: true,
  }),
});
if (glossaryCreated.status !== 201) {
  throw new Error("Glossary E2E entry creation failed");
}
const glossaryRecord = await glossaryCreated.json();
await evaluate(`document.querySelector('[data-testid="glossary-nav"]')?.click(); true`);
await waitFor(
  () => evaluate(`location.hash === "#/glossary"
    && document.querySelector('[data-testid="glossary-manager"]')?.innerText.includes("강연자료 E2E")`),
  "standalone bilingual glossary page",
);
const startedGlossaryEdit = await evaluate(`(() => {
  const row = Array.from(document.querySelectorAll('.glossary-item')).find(
    (el) => el.textContent.includes('강연자료 E2E'),
  );
  if (!row) return false;
  row.querySelector('[data-testid="glossary-edit"]')?.click();
  return true;
})()`);
if (!startedGlossaryEdit) throw new Error("Glossary edit action not accessible");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="glossary-term"]')?.value === "강연자료 E2E"`),
  "glossary edit prefill",
);
const changedJapanese = await evaluate(`(() => {
  const field = document.querySelector('[data-testid="glossary-translation-ja"]');
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (!field || !set) return false;
  set.call(field, '講演資料');
  field.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`);
if (!changedJapanese) throw new Error("Glossary Japanese field change failed");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="glossary-translation-ja"]')?.value === '講演資料'`),
  "glossary Japanese React state",
);
await evaluate(`document.querySelector('[data-testid="glossary-save"]')?.click(); true`);
await waitFor(async () => {
  const response = await fetch("http://127.0.0.1:8000/api/v1/glossary");
  if (!response.ok) return false;
  const entry = (await response.json()).find((row) => row.id === glossaryRecord.id);
  return entry?.translations.en === "lecture notes"
    && entry?.translations.ja === "講演資料"
    && entry?.presets?.[0] === "lecture";
}, "glossary bilingual update persisted");
await navigate("http://127.0.0.1:5173/#/glossary");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="glossary-workspace"]')
    && document.body.innerText.includes("講演資料")`),
  "glossary deep link reload persistence",
);
await evaluate(`document.querySelector('.glossary-workspace-page nav button')?.click(); true`);
await waitFor(
  () => evaluate(`location.hash === '' && !!document.querySelector('[data-testid="session-toggle"]')`),
  "glossary back to live",
);
const deletedGlossary = await fetch(
  `http://127.0.0.1:8000/api/v1/glossary/${encodeURIComponent(glossaryRecord.id)}`,
  { method: "DELETE" },
);
if (deletedGlossary.status !== 204) throw new Error("Glossary E2E cleanup failed");

// Consent is explicitly opt-in and must remain unavailable in demo-only mode.
const defaultRecordingDisabled = await evaluate(`(() => {
  const input = document.querySelector('.recording-opt-in input[type="checkbox"]');
  return Boolean(input) && !input.checked;
})()`);
if (!defaultRecordingDisabled) {
  throw new Error("recording consent must be off for a new session");
}

// A real Project API/UI contract: select the event before starting the live session.
const projectResponse = await fetch("http://127.0.0.1:8000/api/v1/projects", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    title: "Browser E2E conference",
    description: "Project/session grouping acceptance",
    event_date: "2026-10-12",
    location: "Test venue",
    status: "planning",
  }),
});
if (projectResponse.status !== 201) {
  throw new Error(`project creation failed: ${projectResponse.status} ${await projectResponse.text()}`);
}
const eventProject = await projectResponse.json();
await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="session-project-select"] option[value="${eventProject.project_id}"]') !== null`),
  "new event project in operator setup",
);
const projectSelected = await evaluate(`(() => {
  const select = document.querySelector('[data-testid="session-project-select"]');
  if (!select) return false;
  select.value = ${JSON.stringify(eventProject.project_id)};
  select.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
})()`);
if (!projectSelected) throw new Error("event project was not selectable");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="session-project-select"]')?.value === ${JSON.stringify(eventProject.project_id)}`),
  "selected project for the next session",
);

const engineChanged = await evaluate(`(() => {
  const select = document.querySelector('[data-testid="engine-select"]');
  if (!select) return false;
  select.value = "mock";
  select.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
})()`);
if (!engineChanged) throw new Error("ASR engine selector not found");

await waitFor(
  () => evaluate(`(() => {
    const input = document.querySelector('.recording-opt-in input[type="checkbox"]');
    return Boolean(input) && !input.checked && input.disabled;
  })()`),
  "recording consent must be disabled for mock demo engine",
);

await waitFor(
  () => evaluate(`(() => {
    const select = document.querySelector('[data-testid="engine-select"]');
    const button = document.querySelector('[data-testid="session-toggle"]');
    return select?.value === "mock" && Boolean(button) && !button.disabled;
  })()`),
  "mock engine state and enabled session start",
);

await evaluate(`document.querySelector('[data-testid="operator-metrics-consent"]').click()`);

const keyboardFocus = await evaluate(`(() => {
  const button = document.querySelector('[data-testid="session-toggle"]');
  if (!button || button.disabled) return false;
  button.focus();
  return document.activeElement === button;
})()`);
if (!keyboardFocus) throw new Error("session start control could not receive keyboard focus");
await pressEnter();

await waitFor(
  () => evaluate(`document.querySelector('[data-testid="session-toggle"]')?.textContent?.includes("자막 중지")`),
  "keyboard session start",
);
await screenshot("operator-live-ko");

const stateResponse = await fetch("http://127.0.0.1:8000/api/v1/state");
if (!stateResponse.ok) throw new Error(`state request failed: ${stateResponse.status}`);
const state = await stateResponse.json();
if (!state.collect_audience_metrics) throw new Error("operator metrics opt-in did not reach session");
if (!state.running || !state.join_code || !state.session_id) {
  throw new Error(`invalid running state: ${JSON.stringify(state)}`);
}

const savedSessionResponse = await fetch(
  `http://127.0.0.1:8000/api/v1/history/${encodeURIComponent(state.session_id)}`,
);
if (!savedSessionResponse.ok) throw new Error("new session history not available");
const savedSession = await savedSessionResponse.json();
if (savedSession.project_id !== eventProject.project_id) {
  throw new Error("new session was not atomically associated with the selected project");
}

await navigate(`http://127.0.0.1:5173/audience/${state.join_code}`);
await waitFor(
  () => evaluate(`document.body.innerText.includes("LIVE") && document.documentElement.lang === "ko"`),
  "audience live view",
);
await screenshot("audience-ko");
const metricsUrl = "http://127.0.0.1:8000/api/v1/metrics/audience-render";
if (Object.keys((await (await fetch(metricsUrl)).json()).stages).length !== 0) {
  throw new Error("audience reported metrics before its own consent");
}
await evaluate(`(async () => {
  document.querySelector('[data-testid="audience-metrics-consent"]').click();
  await new Promise(resolve => setTimeout(resolve, 50));
  window.dispatchEvent(new CustomEvent('langtextflow:caption-render-metric', {
    detail: { stage: 'stable', receiveToPaintMs: 25, segmentId: 'never-upload-this-id' }
  }));
})()`);
await waitFor(async () => {
  const result = await (await fetch(metricsUrl)).json();
  return result.reporting_enabled && Object.values(result.stages).some(stage => stage.count > 0);
}, "opted-in audience browser report reaches authorized aggregation");
await setViewport(390, 844, true);
await screenshot("audience-mobile-ko");
const audienceIsolation = await evaluate(`(() => {
  const fits = document.documentElement.scrollWidth <= window.innerWidth + 1;
  const noOperatorControls = !document.querySelector('.control-panel') && !document.querySelector('[data-testid="session-toggle"]');
  return fits && noOperatorControls;
})()`);
if (!audienceIsolation) throw new Error("audience view leaked operator UI or overflows horizontally");
await setViewport(1440, 1000, false);

await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('[data-testid="audience-render-latency"]')?.textContent.includes(': p95')`),
  "operator displays the audience aggregate",
);

const stopResponse = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
if (!stopResponse.ok) throw new Error(`session stop failed: ${stopResponse.status}`);
const stopped = await stopResponse.json();
if (stopped.running) throw new Error("session remained running after stop");
if (Object.keys((await (await fetch(metricsUrl)).json()).stages).length !== 0) {
  throw new Error("audience observations survived the session transition");
}

// Synthetic 2-minute WAV checks the finished-session recording import/playback
// contract without involving a real microphone or storing external media.
const pcm = Buffer.alloc(16000 * 2 * 120);
const wav = Buffer.alloc(44 + pcm.length);
wav.write("RIFF", 0, "ascii");
wav.writeUInt32LE(36 + pcm.length, 4);
wav.write("WAVE", 8, "ascii");
wav.write("fmt ", 12, "ascii");
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(16000, 24);
wav.writeUInt32LE(16000 * 2, 28);
wav.writeUInt16LE(2, 32);
wav.writeUInt16LE(16, 34);
wav.write("data", 36, "ascii");
wav.writeUInt32LE(pcm.length, 40);
pcm.copy(wav, 44);
const archiveRoot = `http://127.0.0.1:8000/api/v1/history/${encodeURIComponent(state.session_id)}`;
async function generateSummaryInBrowser(model, transcriptId = "") {
  await waitFor(() => evaluate(`Array.from(document.querySelectorAll('.history-post-event > button'))
    .some(button => button.textContent === '요약 생성 / 다시 생성' && !button.disabled)`),
  "summary generation controls ready");
  await evaluate(`(() => {
    const input = document.querySelector('.history-post-event > .history-post-actions input[type="text"]');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(model)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const source = document.querySelector('[data-testid="summary-transcript-source"]');
    source.value = ${JSON.stringify(transcriptId)};
    source.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitFor(() => evaluate(`document.querySelector('.history-post-event > .history-post-actions input[type="text"]').value === ${JSON.stringify(model)}`), "summary model selection");
  await evaluate(`Array.from(document.querySelectorAll('.history-post-event > button'))
    .find(button => button.textContent === '요약 생성 / 다시 생성').click()`);
  await waitFor(async () => {
    const summary = (await (await fetch(`${archiveRoot}/archive`)).json()).summary;
    if (summary?.model !== model) return false;
    if (summary.status === "error") throw new Error(`Synthetic provider failed: ${summary.error}`);
    return summary.status === "completed";
  }, "actual provider summary job completes");
  const summary = (await (await fetch(`${archiveRoot}/archive`)).json()).summary;
  if (summary.provider !== "ollama" || summary.transcript_id !== (transcriptId || null)
    || !summary.prompt_version.startsWith("post-event-") || !summary.transcript_sha256) {
    throw new Error("Browser summary did not retain real job/source provenance");
  }
}
const upload = await fetch(`${archiveRoot}/recordings`, {
  method: "POST",
  headers: { "Content-Type": "audio/wav" },
  body: wav,
});
if (upload.status !== 201) throw new Error(`WAV archive import failed: ${upload.status} ${await upload.text()}`);
const importedRecording = await upload.json();
if (importedRecording.session_id !== state.session_id ||
    Math.abs(importedRecording.duration_seconds - 120) > 0.001) {
  throw new Error("WAV duration or session metadata mismatch");
}
const archiveResponse = await fetch(`${archiveRoot}/archive`);
if (!archiveResponse.ok) throw new Error("finished session archive API not available");
const archive = await archiveResponse.json();
if (!archive.recordings.some((item) => item.recording_id === importedRecording.recording_id)) {
  throw new Error("imported audio not linked to the finished session");
}
const playback = await fetch(
  `${archiveRoot}/recordings/${encodeURIComponent(importedRecording.recording_id)}/audio`,
);
if (!playback.ok || !Buffer.from(await playback.arrayBuffer()).equals(wav)) {
  throw new Error("session recording audio cannot be played without data corruption");
}

// Move/detach must not delete or rewrite the original session.
const sessionProjectUrl = `http://127.0.0.1:8000/api/v1/history/${encodeURIComponent(state.session_id)}/project`;
for (const projectId of [null, eventProject.project_id]) {
  const linked = await fetch(sessionProjectUrl, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!linked.ok) throw new Error(`project relinking failed: ${linked.status}`);
  const updated = await linked.json();
  if (updated.project_id !== projectId || updated.session_id !== state.session_id) {
    throw new Error("project reassignment changed the wrong session");
  }
}

// Seed only the decoder output; summaries must use the actual HTTP provider/job path.
const offlineTranscriptId = JSON.parse(execFileSync("uv", ["run", "python", "-c", `
import json, sys
from langtextflow.config import get_settings
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_transcripts import TranscriptRequest
sid, rid = json.load(sys.stdin)
settings = get_settings()
archive = PostEventArchive(settings.database_path)
recording = next(r for r in archive.recordings(sid) if r["recording_id"] == rid)
version = archive.transcripts.begin(sid, recording, TranscriptRequest(recording_id=rid))
archive.transcripts.finish(version["transcript_id"], segments=[{
    "segment_id":"offline-000001", "start_ms":1000, "end_ms":2000,
    "text":"Synthetic offline transcript",
}], provenance={"synthetic_e2e": True})
print(json.dumps(version["transcript_id"]))
`], { input: JSON.stringify([state.session_id, importedRecording.recording_id]), encoding: "utf8" }).trim());

await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('.history-item') !== null`),
  "session history record after stop",
);
if (!await evaluate(`document.querySelector('[data-testid="audience-render-latency"]') !== null`)) {
  throw new Error("operator audience metrics display is missing");
}
const historyOpened = await evaluate(`(() => {
  const button = document.querySelector('.history-open');
  if (!button) return false;
  button.focus();
  button.click();
  return true;
})()`);
if (!historyOpened) throw new Error("history detail control was not available");
await waitFor(
  () => evaluate(`document.querySelector('.history-detail') !== null`),
  "history detail",
);
await waitFor(
  () => evaluate(`document.querySelector('.history-post-event audio') !== null`),
  "paired recording player in completed event history",
);
await screenshot("history-detail-ko");

await generateSummaryInBrowser("synthetic-e2e");
await waitFor(
  () => evaluate(`document.querySelector('.history-post-summary textarea')?.value.startsWith('Synthetic summary')`),
  "summary review editor",
);
await evaluate(`(() => {
  const editor = document.querySelector('.history-post-summary textarea');
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(editor, editor.value.replace('Synthetic summary', 'Reviewed summary'));
  editor.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await evaluate(`Array.from(document.querySelectorAll('.history-post-summary button')).find(b => b.textContent === '검수 초안 저장').click()`);
await waitFor(async () => {
  const result = await fetch(`${archiveRoot}/archive`);
  return (await result.json()).summary?.review?.content.startsWith("Reviewed summary");
}, "persisted review draft");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('.history-post-summary button')).some(b => b.textContent === '요약 승인 및 잠금' && !b.disabled)`), "review save completed");
await evaluate(`Array.from(document.querySelectorAll('.history-post-summary button')).find(b => b.textContent === '요약 승인 및 잠금').click()`);
await waitFor(() => evaluate(`document.querySelector('.history-post-summary textarea')?.readOnly === true`), "approved summary locked");
const reviewedArchive = await (await fetch(`${archiveRoot}/archive`)).json();
if (!reviewedArchive.summary.review.approved || !reviewedArchive.summary.content.startsWith("Synthetic summary")) {
  throw new Error("summary approval did not preserve original model draft");
}
await evaluate(`(() => {
  const input = document.querySelector('.history-detail-controls input');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'no-matching-source');
  input.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await waitFor(() => evaluate(`document.querySelectorAll('.history-transcript-segment').length === 0`), "filtered transcript before source navigation");
await evaluate(`document.querySelector('[data-testid="summary-source-link"]').click()`);
await waitFor(
  () => evaluate(`document.activeElement?.classList.contains('history-transcript-segment') && document.querySelector('.history-detail-controls input')?.value === ''`),
  "summary citation clears the filter and focuses its original transcript segment",
);
await waitFor(() => evaluate(`document.querySelector('[data-testid="offline-transcript-text"]')?.value === 'Synthetic offline transcript'`), "offline decoder draft");
await evaluate(`(() => {
  const editor = document.querySelector('[data-testid="offline-transcript-text"]');
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(editor, 'Reviewed offline transcript');
  editor.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await evaluate(`Array.from(document.querySelectorAll('[data-testid="offline-transcripts"] button')).find(b => b.textContent === '전사 검수 저장').click()`);
const offlineRoot = `${archiveRoot}/transcripts/${encodeURIComponent(offlineTranscriptId)}`;
await waitFor(async () => (await (await fetch(offlineRoot)).json()).review?.segments[0]?.text === "Reviewed offline transcript", "persisted offline review");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('[data-testid="offline-transcripts"] button')).some(b => b.textContent === '전사 승인 및 잠금' && !b.disabled)`), "offline review save completed");
await evaluate(`Array.from(document.querySelectorAll('[data-testid="offline-transcripts"] button')).find(b => b.textContent === '전사 승인 및 잠금').click()`);
await waitFor(() => evaluate(`document.querySelector('[data-testid="offline-transcript-text"]')?.readOnly === true`), "approved offline transcript locked");
await waitFor(async () => (await (await fetch(`${archiveRoot}/archive`)).json()).summary_stale, "old summary stale after transcript approval");
await waitFor(() => evaluate(`document.querySelector('[data-testid="summary-transcript-source"]')?.options.length === 2`), "approved source offered explicitly");
if (!await evaluate(`document.querySelector('[data-testid="summary-transcript-source"]').value === ''`)) throw new Error("offline approval silently selected summary source");
const offlineExport = await (await fetch(`${offlineRoot}/export`)).json();
if (offlineExport.version.segments[0].text !== "Synthetic offline transcript" ||
    offlineExport.reviews.length !== 2 || !offlineExport.reviews[1].approved) {
  throw new Error("offline export did not preserve original draft and append-only approvals");
}
await generateSummaryInBrowser("synthetic-offline-e2e", offlineTranscriptId);
const providerCalls = await (await fetch(`${process.env.LTF_E2E_OLLAMA_URL}/fixture/calls`)).json();
if (!providerCalls["synthetic-e2e"] || !providerCalls["synthetic-offline-e2e"]) {
  throw new Error("Summary flow bypassed the synthetic HTTP provider");
}
await navigate("http://127.0.0.1:5173/");
await waitFor(() => evaluate(`document.querySelector('.history-open') !== null`), "history after offline summary generation");
await evaluate(`document.querySelector('.history-open').click()`);
await waitFor(() => evaluate(`document.querySelector('[data-testid="open-summary-transcript"]') !== null`), "summary source version navigation");
await evaluate(`document.querySelector('[data-testid="open-summary-transcript"]').click()`);
await waitFor(() => evaluate(`document.querySelector('[data-testid="offline-source-link"]') !== null`), "offline summary exact citations");
await evaluate(`document.querySelector('[data-testid="offline-source-link"]').click()`);
await waitFor(() => evaluate(`document.activeElement?.dataset.testid === 'offline-transcript-text' && document.activeElement.value === 'Reviewed offline transcript'`), "offline citation focuses its reviewed evidence");
await evaluate(`document.querySelector('[data-testid="offline-audio-seek"]').click()`);
await waitFor(() => evaluate(`(() => {
  const audio = Array.from(document.querySelectorAll('audio')).find(item => item.src.includes(${JSON.stringify(importedRecording.recording_id)}));
  return audio && audio.readyState >= 1 && Math.abs(audio.currentTime - 1) < 0.05
    && document.activeElement === audio && audio.paused;
})()`), "offline source seeks its own recording after metadata load without autoplay");
await evaluate(`(async () => {
  const originalFetch = window.fetch;
  let calls = 0;
  window.fetch = (...args) => {
    if (args[0] === ${JSON.stringify(`${archiveRoot}/archive`)} && ++calls === 1) {
      return new Promise(resolve => setTimeout(() => resolve(new Response(
        ${JSON.stringify(JSON.stringify(reviewedArchive))}, { status: 200 }
      )), 350));
    }
    return originalFetch(...args);
  };
  try {
    const refresh = document.querySelector('.history-detail .history-post-event > .history-post-actions button');
    refresh.click(); refresh.click();
    await new Promise(resolve => setTimeout(resolve, 700));
    if (calls !== 2) throw new Error('archive race did not exercise both refresh requests');
  } finally { window.fetch = originalFetch; }
})()`);
if (!await evaluate(`document.querySelector('[data-testid="open-summary-transcript"]') !== null`)) {
  throw new Error("late old archive response replaced the newer approved transcript source");
}
await cdp("Runtime.evaluate", { expression: await readFile("apps/web/node_modules/axe-core/axe.min.js", "utf8") });
const historyViolations = await evaluate(`axe.run(document, {
  runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] }
}).then(result => result.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })))`);
if (historyViolations.length) throw new Error("completed archive accessibility: " + JSON.stringify(historyViolations));
await setViewport(390, 844, true);
if (!await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`)) {
  throw new Error("expanded event archive overflowed the mobile viewport");
}
await screenshot("history-mobile-ko");
await setViewport(1440, 1000, false);

for (const format of ["srt", "vtt", "txt", "json"]) {
  const exportResponse = await fetch(
    `http://127.0.0.1:8000/api/v1/history/${encodeURIComponent(state.session_id)}/export?format=${format}`,
  );
  if (!exportResponse.ok) throw new Error(`${format} export failed: ${exportResponse.status}`);
  const disposition = exportResponse.headers.get("content-disposition") || "";
  if (!disposition.toLowerCase().includes(`.${format}`)) {
    throw new Error(`${format} export did not advertise the expected filename`);
  }
  await exportResponse.arrayBuffer();
}

// #86: completed three-session project, real HTTP summary jobs and owned fake
// provider; no project model draft or per-session summary is inserted in SQLite.
const extraProjectSessions = [];
for (let index = 0; index < 2; index++) {
  const started = await fetch("http://127.0.0.1:8000/api/v1/session/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine: "mock", project_id: eventProject.project_id,
      source_language: index ? "ja" : "en", target_languages: ["ko"],
      correction_provider: "none", translation_provider: "none",
      context: { title: `Synthetic project source ${index + 2}` } }),
  });
  if (!started.ok) throw new Error("project source session failed to start");
  const session = await started.json();
  extraProjectSessions.push(session.session_id);
  const sessionRoot = `http://127.0.0.1:8000/api/v1/history/${encodeURIComponent(session.session_id)}`;
  await waitFor(async () => (await (await fetch(`${sessionRoot}/captions`)).json()).some(segment => segment.committed), "synthetic project committed source");
  const stopped = await fetch("http://127.0.0.1:8000/api/v1/session/stop", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_session_id: session.session_id }),
  });
  if (!stopped.ok) throw new Error("project source session failed to stop");
  const generated = await fetch(`${sessionRoot}/summary`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "ollama", model: "synthetic-e2e", language: "en" }),
  });
  if (!generated.ok) throw new Error("project source summary failed admission");
  await waitFor(async () => (await (await fetch(`${sessionRoot}/archive`)).json()).summary?.status === "completed", "actual per-session summary dependency");
}
await navigate("http://127.0.0.1:5173/");
await setLocale("ko");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('.event-project-options button')).some(button => button.textContent.includes('Browser E2E conference'))`), "project workspace grouping");
await evaluate(`Array.from(document.querySelectorAll('.event-project-options button')).find(button => button.textContent.includes('Browser E2E conference')).click()`);
const projectPanel = `[data-testid="project-digest"]`;
const knowledgePanel = '[data-testid="project-knowledge"]';
const knowledgeRoot = `http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}/knowledge`;
await evaluate("document.querySelector('.preflight-close')?.click()");
await waitFor(() => evaluate("document.querySelector('.preflight-panel') === null"), "dismissed preflight overlay before material editing");
const materialButton = label => evaluate(`Array.from(document.querySelectorAll('${knowledgePanel} button')).find(button => button.textContent === ${JSON.stringify(label)})?.click()`);
async function materialInput(selector, value) {
  await evaluate(`(() => { const el = document.querySelector('${knowledgePanel} ${selector}');
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', {bubbles:true})); })()`);
}
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('${knowledgePanel} button')).some(button => button.textContent === '자료 추가' && !button.disabled)`), "project materials loaded");
await materialButton("자료 추가");
await waitFor(() => evaluate(`document.querySelector('${knowledgePanel} select') !== null`), "material editor");
await materialInput("select", "handout");
await materialInput('input:not([type="file"]):not([type="checkbox"])', "Owned UTF-8 handout");
const handoutText = "Owned synthetic handout: 성령 / 原文 🙏\nOperator context, never spoken evidence.\n";
const handoutDirectory = await mkdtemp(join(tmpdir(), "ltf-project-handout-"));
try {
  const handoutPath = join(handoutDirectory, "owned.md");
  await writeFile(handoutPath, handoutText, "utf8");
  const dom = await cdp("DOM.getDocument");
  const input = await cdp("DOM.querySelector", { nodeId: dom.root.nodeId, selector: `${knowledgePanel} input[type="file"]` });
  await cdp("DOM.setFileInputFiles", { nodeId: input.nodeId, files: [handoutPath] });
  await waitFor(() => evaluate(`document.querySelector('${knowledgePanel} textarea')?.value === ${JSON.stringify(handoutText)}`), "review original UTF-8 text before persistence");
  await materialButton("자료 저장");
} finally { await rm(handoutDirectory, { recursive:true, force:true }); }
let handout;
await waitFor(async () => {
  handout = (await (await fetch(knowledgeRoot)).json())[0];
  return handout?.text === handoutText && handout.revision === 1;
}, "actual authored material SQLite write");
// Each material type has its own explicit author label; a TODO never comes from AI.
for (const kind of ["brief", "agenda", "speaker", "decision", "todo"]) {
  const response = await fetch(knowledgeRoot, { method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({kind,title:`Owned ${kind}`,text:`Operator-authored synthetic ${kind}`,done:kind === "todo"}) });
  if (!response.ok) throw new Error(`material type rejected: ${kind}`);
}
await materialButton("자료 다시 불러오기");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('${projectPanel} button')).some(button => button.textContent === '프로젝트 요약 생성 / 다시 시도' && !button.disabled)`), "project evidence dependencies and transmission policy");
await evaluate(`(() => {
  const input = document.querySelector('${projectPanel} input:not([type="checkbox"])');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'synthetic-project-e2e');
  input.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await waitFor(() => evaluate(`document.querySelector('${projectPanel} input:not([type="checkbox"])').value === 'synthetic-project-e2e'`), "project model selection");
await evaluate(`Array.from(document.querySelectorAll('${projectPanel} button')).find(button => button.textContent === '프로젝트 요약 생성 / 다시 시도').click()`);
const projectRoot = `http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}/digests`;
let projectVersion;
await waitFor(async () => {
  const data = await (await fetch(projectRoot)).json();
  projectVersion = data.versions[0];
  if (projectVersion?.status === "error") throw new Error(projectVersion.error);
  return projectVersion?.status === "completed";
}, "actual project model job completion");
await waitFor(() => evaluate(`document.querySelector('${projectPanel} textarea')?.value.startsWith('Synthetic project chronology')`), "project review editor");
await evaluate(`Array.from(document.querySelectorAll('${projectPanel} button')).find(button => button.textContent === '검수 초안 저장').click()`);
const projectVersionRoot = `${projectRoot}/${projectVersion.digest_id}`;
await waitFor(async () => (await (await fetch(projectVersionRoot)).json()).review?.review_id, "saved project review");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('${projectPanel} button')).some(button => button.textContent === '요약 승인 및 잠금' && !button.disabled)`), "project review save completed");
await evaluate(`Array.from(document.querySelectorAll('${projectPanel} button')).find(button => button.textContent === '요약 승인 및 잠금').click()`);
await waitFor(() => evaluate(`document.querySelector('${projectPanel} textarea')?.readOnly === true`), "project approval lock");
let projectExport = await (await fetch(`${projectVersionRoot}/export`)).json();
if (projectExport.version.snapshot.sessions.length !== 3 || projectExport.reviews.length !== 2
  || !projectExport.reviews[1].approved || !projectExport.version.content.startsWith("Synthetic project chronology")
  || projectExport.version.snapshot.sessions[0].transcript_id !== offlineTranscriptId) {
  throw new Error("project version/evidence/review export lost provenance");
}
if (projectExport.version.snapshot.knowledge.length !== 6 || projectExport.version.snapshot.source.includes(handoutText)) throw new Error("authored materials became spoken evidence");
// Edit through the real form after approval: frozen original stays unchanged,
// new input becomes stale, and explicit provider consent resets with the panel.
await evaluate(`Array.from(document.querySelectorAll('${knowledgePanel} li')).find(li => li.querySelector('h4')?.textContent === 'Owned UTF-8 handout').querySelector('button').click()`);
await waitFor(() => evaluate(`document.querySelector('${knowledgePanel} textarea') !== null`), "edit material");
const revisedHandout = handoutText + "Operator revision 2.\n";
await materialInput("textarea", revisedHandout); await materialButton("자료 저장");
await waitFor(async () => (await (await fetch(projectVersionRoot)).json()).stale, "material revision invalidates approved project digest");
if ((await (await fetch(projectVersionRoot)).json()).snapshot.knowledge[0].text !== handoutText) throw new Error("material edit rewrote historical digest input");
projectExport = await (await fetch(`${projectVersionRoot}/export`)).json();
const projectProviderCalls = await (await fetch(`${process.env.LTF_E2E_OLLAMA_URL}/fixture/calls`)).json();
if (!(projectProviderCalls["synthetic-project-e2e"] >= 3)) throw new Error("project synthesis bypassed hierarchical HTTP provider");
if (!await evaluate(`document.querySelectorAll('${projectPanel} ul button').length >= 3`)) throw new Error("project source citations missing");
await evaluate(`document.querySelector('${projectPanel} ul button').click()`);
await waitFor(() => evaluate(`document.querySelector('.history-detail') !== null`), "project source-session navigation");
await cdp("Runtime.evaluate", { expression: await readFile("apps/web/node_modules/axe-core/axe.min.js", "utf8") });
const projectViolations = await evaluate(`axe.run(document.querySelector('${projectPanel}'), { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } }).then(result => result.violations.map(item => item.id))`);
if (projectViolations.length) throw new Error("project digest accessibility: " + JSON.stringify(projectViolations));
const knowledgeViolations = await evaluate(`axe.run(document.querySelector('${knowledgePanel}'), { runOnly: {type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']} }).then(result=>result.violations.map(item=>item.id))`);
if (knowledgeViolations.length) throw new Error("project materials accessibility: " + JSON.stringify(knowledgeViolations));
async function materialScreenshot(name) {
  await evaluate(`document.querySelector('${knowledgePanel}').scrollIntoView({block:'start'}); true`);
  await sleep(100);
  const clip = await evaluate(`(() => { const r=document.querySelector('${knowledgePanel}').getBoundingClientRect(); return {x:Math.max(0,r.x+scrollX),y:Math.max(0,r.y+scrollY),width:Math.min(r.width,innerWidth),height:Math.min(r.height,innerHeight-100),scale:1}; })()`);
  const result=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,clip});
  await writeFile(`artifacts/${name}.png`,Buffer.from(result.data,'base64'));
}
await materialScreenshot("project-materials-desktop-ko");
await setViewport(390, 844, true);
if (!await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`)) throw new Error("project digest mobile overflow");
await screenshot("project-digest-mobile-ko");
await materialScreenshot("project-materials-mobile-ko");
await setViewport(1440, 1000, false);

// Full private package: native attachment download, rejected duplicate restore,
// then restore the same three-language graph after removing only our fixtures.
// No DB seeding or model output invention is used for the backup/restore path.
const packageDirectory = await mkdtemp(join(tmpdir(), "ltf-private-package-e2e-"));
try {
  const packagePanel = '[data-testid="project-package"]';
  const secondRecording = await fetch(`http://127.0.0.1:8000/api/v1/history/${extraProjectSessions[0]}/recordings`, {
    method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav,
  });
  if (!secondRecording.ok) throw new Error("second package recording fixture failed");
  const packageButton = label => evaluate(`Array.from(document.querySelectorAll('${packagePanel} button')).find(button => button.textContent === ${JSON.stringify(label)})?.click()`);
  await packageButton("전체 백업 준비");
  await waitFor(() => evaluate(`document.querySelector('${packagePanel} a[download]') !== null`), "private package prepared");
  const prepared = (await (await fetch("http://127.0.0.1:8000/api/v1/project-packages")).json()).prepared;
  await cdp("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: packageDirectory });
  await evaluate(`document.querySelector('${packagePanel} a[download]').click()`);
  const packagePath = join(packageDirectory, "langtextflow-private-project.zip");
  await waitFor(async () => (await stat(packagePath)).size === prepared.size_bytes, "native private ZIP download");
  const packageBytes = await readFile(packagePath);
  if (createHash("sha256").update(packageBytes).digest("hex") !== prepared.sha256) throw new Error("native package download checksum changed");
  const graphFromPackage = JSON.parse(execFileSync("uv", ["run", "python", "-c", `
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as package:
    graph = json.loads(package.read('graph.json'))
print(json.dumps(graph))
`, packagePath], { encoding: "utf8" }));
  if (graphFromPackage.tables.sessions.length !== 3 || graphFromPackage.tables.session_recordings.length !== 2
    || graphFromPackage.tables.project_digest_reviews.length !== 2
    || graphFromPackage.tables.approved_transcripts.length !== 1
    || graphFromPackage.tables.project_knowledge.length !== 6
    || graphFromPackage.tables.project_knowledge[0].text !== revisedHandout) throw new Error("whole package silently omitted evidence");
  const document = await cdp("DOM.getDocument");
  const input = await cdp("DOM.querySelector", { nodeId: document.root.nodeId, selector: `${packagePanel} input[type="file"]` });
  await cdp("DOM.setFileInputFiles", { nodeId: input.nodeId, files: [packagePath] });
  await waitFor(() => evaluate(`!Array.from(document.querySelectorAll('${packagePanel} button')).find(button => button.textContent === '선택한 백업 복원')?.disabled`), "chosen authorized backup file");
  await packageButton("선택한 백업 복원");
  await waitFor(() => evaluate(`document.querySelector('${packagePanel} [role="alert"]')?.textContent.includes('already exists')`), "existing ID restore rejected");
  const preserved = await (await fetch(`${projectVersionRoot}/export`)).json();
  if (JSON.stringify(preserved) !== JSON.stringify(projectExport)) throw new Error("collision modified existing project");
  for (const sid of [state.session_id, ...extraProjectSessions]) {
    if (!(await fetch(`http://127.0.0.1:8000/api/v1/history/${sid}`, { method: "DELETE" })).ok) throw new Error("owned source deletion failed");
  }
  if (!(await fetch(`http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}`, { method: "DELETE" })).ok) throw new Error("owned project deletion failed");
  // A deleted recording's durable tombstone can briefly remain until its bounded
  // recovery worker finishes. UI retries remain explicit and cannot overwrite it.
  let restored = false;
  for (let attempt = 0; attempt < 10; attempt++) {
    await fetch("http://127.0.0.1:8000/api/v1/project-packages");
    await packageButton("선택한 백업 복원");
    await waitFor(() => evaluate(`!Array.from(document.querySelectorAll('${packagePanel} button')).find(button => button.textContent === '선택한 백업 복원')?.disabled || document.querySelector('${packagePanel} [role="status"]')?.textContent.includes('복원이 완료')`), "restore request finished");
    const check = await fetch(`http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}`);
    if (check.ok) { restored = true; break; }
    await sleep(200);
  }
  if (!restored) throw new Error("private package UI restore failed");
  await waitFor(() => evaluate(`document.querySelector('${packagePanel}')?.textContent.includes('프로젝트 복원이 완료되었습니다.')`), "UI restore success");
  const recovered = await (await fetch(`${projectVersionRoot}/export`)).json();
  if (JSON.stringify(recovered) !== JSON.stringify(projectExport)) throw new Error("project snapshot bytes/hash/reviews/provenance changed during restore");
  const restoredMaterials = await (await fetch(knowledgeRoot)).json();
  if (restoredMaterials.length !== 6 || restoredMaterials[0].knowledge_id !== handout.knowledge_id
    || restoredMaterials[0].text !== revisedHandout || restoredMaterials[0].revision !== 2) throw new Error("material original/identity/revision lost during restore");
  for (const rec of graphFromPackage.tables.session_recordings) {
    const response = await fetch(`http://127.0.0.1:8000/api/v1/history/${rec.session_id}/recordings/${rec.recording_id}/audio`);
    if (!response.ok || createHash("sha256").update(Buffer.from(await response.arrayBuffer())).digest("hex") !== rec.sha256) throw new Error("restored WAV bytes or recording identity changed");
  }
  const restoredState = await (await fetch("http://127.0.0.1:8000/api/v1/state")).json();
  if (restoredState.running) throw new Error("restore activated a historical session");
  await cdp("Runtime.evaluate", { expression: await readFile("apps/web/node_modules/axe-core/axe.min.js", "utf8") });
  const violations = await evaluate(`axe.run(document.querySelector('${packagePanel}'), { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } }).then(result => result.violations.map(item => item.id))`);
  if (violations.length) throw new Error("private package accessibility: " + JSON.stringify(violations));
  await setViewport(390, 844, true);
  if (!await evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")) throw new Error("private package mobile overflow");
  await screenshot("project-package-restored-mobile-ko");
  await setViewport(1440, 1000, false);
  console.log("private project package E2E: native ZIP download/SHA, duplicate409, empty-project UI restore, 3 language sessions/2 WAVs/stable IDs/frozen digest/reviews, inactive session, mobile/axe");
} finally {
  await cdp("Browser.setDownloadBehavior", { behavior: "default" });
  await rm(packageDirectory, { recursive: true, force: true });
}
const movedSource = await fetch(`http://127.0.0.1:8000/api/v1/history/${extraProjectSessions[0]}/project`, {
  method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project_id: null }),
});
if (!movedSource.ok) throw new Error("project move invalidation fixture failed");
const movedDigest = await (await fetch(projectVersionRoot)).json();
if (!movedDigest.stale || movedDigest.evidence_available || movedDigest.content !== ""
  || (await fetch(`${projectVersionRoot}/export`)).status !== 409) throw new Error("old project leaked moved-source evidence");
await waitFor(() => evaluate(`Array.from(document.querySelectorAll('${knowledgePanel} li h4')).some(el=>el.textContent==='Owned UTF-8 handout')`), "restored original material visible");
const confirmedDeletion = new Promise(resolve => socket.addEventListener("message", async function onDialog(event) {
  const message=JSON.parse(event.data);
  if (message.method !== 'Page.javascriptDialogOpening') return;
  socket.removeEventListener('message',onDialog);
  if (!message.params.message.includes('프로젝트 요약 버전과 승인')) throw new Error('material deletion impact not shown');
  await cdp('Page.handleJavaScriptDialog',{accept:true}); resolve();
}));
const deletingMaterial = evaluate(`Array.from(document.querySelectorAll('${knowledgePanel} li')).find(li=>li.querySelector('h4')?.textContent==='Owned UTF-8 handout').querySelectorAll('button')[1].click()`);
await confirmedDeletion; await deletingMaterial;
await waitFor(async () => (await fetch(projectVersionRoot)).status === 404, "explicit material deletion removes frozen source copies");
if ((await (await fetch(knowledgeRoot)).json()).length !== 5) throw new Error("material deletion removed unrelated originals");
console.log("project materials E2E: UTF-8 File→reviewed original→revision/SHA, six explicit types, stale locked digest, 3-language package restore, deletion of derived copies, mobile/axe");

// #131: actual browser/HTTP/WS flow with a declared EN source and requested KO
// captions but no translation provider. Mock words exercise the UI routing,
// NOT English ASR accuracy.
const missionaryStart = await fetch("http://127.0.0.1:8000/api/v1/session/start", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    engine: "mock",
    source_language: "en",
    target_languages: ["ko"],
    correction_provider: "none",
    translation_provider: "none",
  }),
});
if (!missionaryStart.ok) {
  throw new Error("missionary-language route smoke start failed: " + missionaryStart.status);
}
const missionaryState = await missionaryStart.json();
try {
  await navigate("http://127.0.0.1:5173/audience/" + missionaryState.join_code + "?lang=ko");
  await waitFor(
    () => evaluate("(() => { const item = document.querySelector('.audience-transcript article span'); const live = document.querySelector('.audience-caption'); return Boolean(item && live) && /한국어 번역 중|한국어 번역을 사용할 수 없습니다/.test(item.textContent || '') && !/하나님이 세상을|요한 보금/.test(live.textContent || ''); })()"),
    "English-source transcript never presented as Korean audience translation",
  );
  const selectedSource = await evaluate("(() => { const select = document.querySelector('.audience-language select'); if (!(select instanceof HTMLSelectElement)) return false; const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set; if (!setter) return false; setter.call(select, 'en'); select.dispatchEvent(new Event('change', { bubbles: true })); return true; })()");
  if (!selectedSource) throw new Error("source caption selection unavailable");
  await waitFor(
    () => evaluate("(() => { const text = document.querySelector('.audience-transcript article span')?.textContent || ''; return document.querySelector('.audience-language select')?.value === 'en' && (text.includes('요한 보금') || text.includes('하나님이 세상을') || text.includes('복음의 의미')); })()"),
    "explicit source selection shows the original mock transcript",
  );
} finally {
  const stop = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
  if (!stop.ok) throw new Error("missionary-language smoke cleanup failed");
}

// A fresh operator page recovers a server-owned session without posting another start.
const hydrationProjectResponse = await fetch("http://127.0.0.1:8000/api/v1/projects", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ title: "Hydration project E2E" }),
});
if (!hydrationProjectResponse.ok) throw new Error("hydration project fixture failed");
const hydrationProject = await hydrationProjectResponse.json();
const hydrationStart = await fetch("http://127.0.0.1:8000/api/v1/session/start", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    project_id: hydrationProject.project_id, engine: "mock", resource_profile: "low",
    source_language: "en", target_languages: ["ko", "ja"],
    translation_provider: "none", correction_provider: "none",
    context: { title: "Recovered live session E2E", presenter: "Recovered speaker", preset: "lecture",
      hotwords: ["Grace-restored"] },
  }),
});
if (!hydrationStart.ok) throw new Error("hydration running fixture failed");
const hydrationSession = await hydrationStart.json();
try {
  await navigate("http://127.0.0.1:5173/?hydrate=running");
  await waitFor(() => evaluate(`(() => {
    const control = document.querySelector('[data-testid="session-toggle"]');
    return control?.classList.contains('stop-button')
      && document.querySelector('[data-testid="session-title-input"]')?.value === 'Recovered live session E2E'
      && document.querySelector('[data-testid="session-presenter-input"]')?.value === 'Recovered speaker'
      && document.querySelector('[data-testid="session-source-select"]')?.value === 'en'
      && document.querySelector('[data-testid="session-preset-select"]')?.value === 'lecture'
      && document.querySelector('[data-testid="resource-profile-select"]')?.value === 'low'
      && document.querySelector('[data-testid="engine-select"]')?.value === 'mock'
      && document.querySelector('[data-testid="session-project-select"]')?.value === ${JSON.stringify(hydrationProject.project_id)}
      && document.querySelector('[data-testid="translation-provider-select"]')?.value === 'none'
      && [...document.querySelectorAll('.target-language-chips button[aria-pressed="true"] b')]
        .map(node => node.textContent).join(',') === 'KO,JA';
  })()`), "reload restores actual session form and stop controls");
  const liveHydrated = await fetch("http://127.0.0.1:8000/api/v1/state").then(response => response.json());
  if (!liveHydrated.running || liveHydrated.session_id !== hydrationSession.session_id) {
    throw new Error("operator reload changed or stopped the server-owned session");
  }
  const externalStop = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
  if (!externalStop.ok) throw new Error("hydration external stop failed");
  await waitFor(() => evaluate(`document.querySelector('[data-testid="session-toggle"]')?.classList.contains('start-button')
    && !document.querySelector('[data-testid="session-title-input"]')?.disabled
    && !document.querySelector('[data-testid="operator-metrics-consent"]')?.checked`),
  "reconnect to ended backend clears stale running controls");
} finally {
  await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
}

// Retention ages only the three previously created owned fixtures. No original
// text, model output or review is seeded: wall-clock expiry is the sole DB edit.
const retentionIds=[state.session_id,...extraProjectSessions];
await fetch(`http://127.0.0.1:8000/api/v1/history/${extraProjectSessions[0]}/project`,{
  method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({project_id:eventProject.project_id}),
});
if(!process.env.LANGTEXTFLOW_DATABASE_PATH)throw new Error("owned retention fixture DB was not provided");
execFileSync(process.env.UV_PROJECT_ENVIRONMENT?`${process.env.UV_PROJECT_ENVIRONMENT}/bin/python`:"python",["-c",`
import datetime,json,pathlib,sqlite3,sys
db=pathlib.Path(sys.argv[1]);ids=json.loads(sys.argv[2])
with sqlite3.connect(db) as conn:
    for sid in ids:
        row=conn.execute('SELECT p.title FROM projects p JOIN project_sessions x ON x.project_id=p.project_id WHERE x.session_id=?',(sid,)).fetchone()
        assert row and row[0]=='Browser E2E conference','not an owned browser fixture'
    now=datetime.datetime.now(datetime.timezone.utc)
    for sid in ids:
        conn.execute('UPDATE sessions SET started_at=?,ended_at=? WHERE session_id=?',((now-datetime.timedelta(days=91)).isoformat(),(now-datetime.timedelta(days=90)).isoformat(),sid))
unknown=db.parent/'recordings'/'retention-unknown.wav'
assert not unknown.exists()
unknown.write_bytes(b'owned unknown-file negative control')
`,process.env.LANGTEXTFLOW_DATABASE_PATH,JSON.stringify(retentionIds)]);
const retentionLiveStart=await fetch("http://127.0.0.1:8000/api/v1/session/start",{
  method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({engine:"mock",project_id:eventProject.project_id,source_language:"en",target_languages:["ko"],translation_provider:"none",correction_provider:"none",context:{title:"Retention protected live fixture"}}),
});
if(!retentionLiveStart.ok)throw new Error("retention live protection fixture failed");
const retentionLive=await retentionLiveStart.json();
try {
  const root=`http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}/retention`;
  const initial=await(await fetch(root)).json();
  if(initial.policy.days!==null||initial.receipts.length)throw new Error("retention default was destructive");
  const newDigest=await fetch(projectRoot,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:"synthetic-project-e2e"})});
  if(!newDigest.ok)throw new Error("retention project digest fixture failed");
  const newVersion=await newDigest.json();
  await waitFor(async()=>(await(await fetch(`${projectRoot}/${newVersion.digest_id}`)).json()).status==="completed","actual current digest before retention");
  const glossaryRoot=`http://127.0.0.1:8000/api/v1/projects/${eventProject.project_id}/glossary`;
  if(!(await fetch(glossaryRoot,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({term:"retention-owned",canonical:"retention-owned",source_language:"en"})})).ok)throw new Error("retention glossary fixture failed");
  const materialCount=(await(await fetch(knowledgeRoot)).json()).length;
  await navigate("http://127.0.0.1:5173/?retention=owned");await setLocale("ko");
  await waitFor(()=>evaluate(`Array.from(document.querySelectorAll('.event-project-options button')).some(b=>b.textContent.includes('Browser E2E conference'))`),"retention project picker");
  await evaluate(`Array.from(document.querySelectorAll('.event-project-options button')).find(b=>b.textContent.includes('Browser E2E conference')).click()`);
  await evaluate("document.querySelector('.preflight-close')?.click()");
  const panel='[data-testid="project-retention"]';
  const button=label=>evaluate(`Array.from(document.querySelectorAll('${panel} button')).find(b=>b.textContent===${JSON.stringify(label)})?.click()`);
  await waitFor(()=>evaluate(`document.querySelector('${panel} select:not(:disabled)')!==null`),"retention form loaded");
  await evaluate(`(()=>{const el=document.querySelector('${panel} select');el.value='days';el.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await button("보존 규칙 저장");
  await waitFor(async()=>(await(await fetch(root)).json()).policy.days===30,"explicit period stored");
  for(const sid of retentionIds)if(!(await fetch(`http://127.0.0.1:8000/api/v1/history/${sid}`)).ok)throw new Error("policy save deleted without explicit apply");
  await waitFor(()=>evaluate(`Array.from(document.querySelectorAll('${panel} button')).some(b=>b.textContent==='만료 보관기록 미리보기'&&!b.disabled)`),"saved policy ready for review");
  await button("만료 보관기록 미리보기");
  await waitFor(()=>evaluate(`document.querySelectorAll('${panel} ul:first-of-type li').length===3`),"three expired archives reviewed");
  const preview=await(await fetch(`${root}/preview`,{method:"POST"})).json();
  if(preview.candidates.length!==3||preview.excluded_running_count!==1||preview.candidates.some(r=>!retentionIds.includes(r.session_id)))throw new Error("retention included foreign/active scope");
  await cdp("Runtime.evaluate",{expression:await readFile("apps/web/node_modules/axe-core/axe.min.js","utf8")});
  const violations=await evaluate(`axe.run(document.querySelector('${panel}'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}).then(r=>r.violations.map(v=>v.id))`);
  if(violations.length)throw new Error("retention accessibility: "+JSON.stringify(violations));
  async function captureRetention(name){
    await evaluate(`document.querySelector('${panel}').scrollIntoView({block:'start'});true`);await sleep(100);
    const clip=await evaluate(`(()=>{const r=document.querySelector('${panel}').getBoundingClientRect();return{x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:Math.min(r.height,innerHeight-150),scale:1};})()`);
    const result=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,clip});await writeFile(`artifacts/${name}.png`,Buffer.from(result.data,'base64'));
  }
  await captureRetention("project-retention-desktop-ko");await setViewport(390,844,true);
  if(!await evaluate("document.documentElement.scrollWidth<=innerWidth+1"))throw new Error("retention mobile overflow");
  await captureRetention("project-retention-mobile-ko");await setViewport(1440,1000,false);
  const confirmed=new Promise(resolve=>socket.addEventListener('message',async function dialog(event){const message=JSON.parse(event.data);if(message.method!=='Page.javascriptDialogOpening')return;socket.removeEventListener('message',dialog);if(!message.params.message.includes('영구 삭제'))throw new Error('deletion consequence missing');await cdp('Page.handleJavaScriptDialog',{accept:true});resolve();}));
  const deleting=button("검토한 보관기록 영구 삭제");await confirmed;await deleting;
  await waitFor(async()=>(await(await fetch(root)).json()).receipts[0]?.deleted_session_count===3,"atomic archive deletion receipt");
  await waitFor(()=>evaluate(`document.querySelectorAll('.history-list .history-item').length===1`),"history list invalidated after explicit deletion");
  for(const sid of retentionIds)if((await fetch(`http://127.0.0.1:8000/api/v1/history/${sid}`)).status!==404)throw new Error("expired original remained available");
  if((await fetch(`${projectRoot}/${newVersion.digest_id}`)).status!==404)throw new Error("deleted source remained in frozen project digest");
  if((await(await fetch(knowledgeRoot)).json()).length!==materialCount||(await(await fetch(glossaryRoot)).json()).length!==1)throw new Error("retention erased unrelated project knowledge");
  const liveState=await(await fetch("http://127.0.0.1:8000/api/v1/state")).json();
  if(!liveState.running||liveState.session_id!==retentionLive.session_id)throw new Error("retention interrupted the live caption source");
  await waitFor(async()=>{const data=await(await fetch(root)).json();return data.receipts[0]?.media_cleanup_pending_count===0;},"verified WAV tombstones cleaned");
  execFileSync(process.env.UV_PROJECT_ENVIRONMENT?`${process.env.UV_PROJECT_ENVIRONMENT}/bin/python`:"python",["-c",`import pathlib,sys;p=pathlib.Path(sys.argv[1]).parent/'recordings'/'retention-unknown.wav';assert p.read_bytes()==b'owned unknown-file negative control'`,process.env.LANGTEXTFLOW_DATABASE_PATH]);
  console.log("retention E2E: explicit rule→three-language/two-WAV reviewed deletion, active source and project knowledge preserved, owned cleanup/unknown-file negative control, mobile/axe");
} finally {await fetch("http://127.0.0.1:8000/api/v1/session/stop",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_session_id:retentionLive.session_id})});}

socket.close();
console.log("browser E2E smoke passed: projects, onboarding, locales, keyboard, audience, history, real summary jobs with synthetic HTTP provider, review/export and language isolation");
