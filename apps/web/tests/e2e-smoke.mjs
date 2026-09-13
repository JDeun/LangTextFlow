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

async function setViewport(width, height, mobile = false) {
  await cdp("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile,
  });
  await sleep(100);
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

await setLocale("ko");
await setViewport(390, 844, true);
await navigate("http://127.0.0.1:5173/");
await waitFor(
  () => evaluate(`document.querySelector('.preflight-pill') !== null && document.querySelector('.preflight-panel') === null`),
  "collapsed mobile preflight",
);
await screenshot("operator-mobile-ko");
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

const startClicked = await evaluate(`(() => {
  const button = document.querySelector('[data-testid="session-toggle"]');
  if (!button || button.disabled) return false;
  button.click();
  return true;
})()`);
if (!startClicked) throw new Error("session start button was not clickable");

await waitFor(
  () => evaluate(`document.querySelector('[data-testid="session-toggle"]')?.textContent?.includes("자막 중지")`),
  "session start",
);
await screenshot("operator-live-ko");

const stateResponse = await fetch("http://127.0.0.1:8000/api/v1/state");
if (!stateResponse.ok) throw new Error(`state request failed: ${stateResponse.status}`);
const state = await stateResponse.json();
if (!state.running || !state.join_code) throw new Error(`invalid running state: ${JSON.stringify(state)}`);

await navigate(`http://127.0.0.1:5173/audience/${state.join_code}`);
await waitFor(
  () => evaluate(`document.body.innerText.includes("LIVE") && document.documentElement.lang === "ko"`),
  "audience live view",
);
await screenshot("audience-ko");
await setViewport(390, 844, true);
await screenshot("audience-mobile-ko");
await setViewport(1440, 1000, false);

const stopResponse = await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
if (!stopResponse.ok) throw new Error(`session stop failed: ${stopResponse.status}`);
const stopped = await stopResponse.json();
if (stopped.running) throw new Error("session remained running after stop");

socket.close();
console.log("browser E2E smoke passed for en/ko/ja locale switching and mobile layout");
