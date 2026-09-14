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
    type: "rawKeyDown",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
  await cdp("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
}

async function setLocale(locale) {
  await evaluate(`localStorage.setItem("langtextflow:locale:v1", ${JSON.stringify(locale)}); location.reload(); true`);
  await waitFor(
    () => evaluate(`document.readyState === "complete" && document.querySelector('[data-testid="locale-select"]')?.value === ${JSON.stringify(locale)}`),
    `locale ${locale}`,
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

socket.close();
console.log("browser E2E smoke passed: onboarding, locale/layout, keyboard start, audience, stop, history and exports");
