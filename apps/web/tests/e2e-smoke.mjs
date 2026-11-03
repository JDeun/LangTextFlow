import { mkdir, readFile, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";

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

// Seed only a deterministic model draft; review/save/approval use real HTTP and SQLite.
const offlineTranscriptId = JSON.parse(execFileSync("uv", ["run", "python", "-c", `
import json, sys
from langtextflow.config import get_settings
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.post_event_summary import transcript_source
from langtextflow.session_repository import SessionRepository
from langtextflow.post_event_transcripts import TranscriptRequest
sid, rid = json.load(sys.stdin)
settings = get_settings()
history = SessionRepository(settings.database_path)
source, digest = transcript_source(history.segments(sid))
archive = PostEventArchive(settings.database_path)
record = archive.begin_summary(sid, language="ko", provider="ollama", model="synthetic-e2e",
    transcript_sha256=digest, prompt_version="synthetic-e2e")
archive.finish_summary(record["summary_id"], content="Synthetic summary " + source.split("]", 1)[0] + "]")
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
// Seed an already generated cited draft; source navigation still uses real archive/detail APIs.
execFileSync("uv", ["run", "python", "-c", `
import json,sys
from langtextflow.config import get_settings
from langtextflow.post_event_archive import PostEventArchive
from langtextflow.session_repository import SessionRepository
sid,tid = json.load(sys.stdin)
settings = get_settings()
archive = PostEventArchive(settings.database_path)
source,digest = archive.transcripts.source(sid, SessionRepository(settings.database_path).segments(sid), tid)
summary = archive.begin_summary(sid,language="ko",provider="ollama",model="synthetic-offline-e2e",
    transcript_sha256=digest,prompt_version="synthetic-offline-e2e",transcript_id=tid,approved_transcript_id=tid)
archive.finish_summary(summary["summary_id"],content="Offline summary [offline-000001 at 1s]")
`], { input: JSON.stringify([state.session_id, offlineTranscriptId]) });
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
    const refresh = document.querySelector('.history-post-event > .history-post-actions button');
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

socket.close();
console.log("browser E2E smoke passed: project create/assign/move, onboarding, locale/layout, keyboard start, audience, stop, history, exports and no wrong-language fallback");
