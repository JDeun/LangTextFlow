import { mkdir, writeFile } from "node:fs/promises";

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
await setViewport(390, 844, true);
await screenshot("audience-mobile-ko");
const audienceIsolation = await evaluate(`(() => {
  const fits = document.documentElement.scrollWidth <= window.innerWidth + 1;
  const noOperatorControls = !document.querySelector('.control-panel') && !document.querySelector('[data-testid="session-toggle"]');
  return fits && noOperatorControls;
})()`);
if (!audienceIsolation) throw new Error("audience view leaked operator UI or overflows horizontally");
await setViewport(1440, 1000, false);

const stopResponse = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
if (!stopResponse.ok) throw new Error(`session stop failed: ${stopResponse.status}`);
const stopped = await stopResponse.json();
if (stopped.running) throw new Error("session remained running after stop");

// Synthetic 1-second WAV checks the finished-session recording import/playback
// contract without involving a real microphone or storing external media.
const pcm = Buffer.alloc(16000 * 2);
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
    Math.abs(importedRecording.duration_seconds - 1) > 0.001) {
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

await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('.history-item') !== null`),
  "session history record after stop",
);
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
