import { readFile } from "node:fs/promises";

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
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser evaluation failed");
  return result.result?.value;
}

async function navigate(url) {
  await cdp("Page.navigate", { url });
  await waitFor(() => evaluate("document.readyState === 'complete'"), `page load: ${url}`);
}

const axeSource = await readFile("apps/web/node_modules/axe-core/axe.min.js", "utf8");

async function assertAccessible(label) {
  await cdp("Runtime.evaluate", { expression: axeSource });
  const violations = await evaluate(`axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }
  }).then((result) => result.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    help: violation.help,
    nodes: violation.nodes.slice(0, 5).map((node) => node.target)
  })))`);
  if (violations.length) {
    throw new Error(`${label} accessibility violations:\n${JSON.stringify(violations, null, 2)}`);
  }
}

await cdp("Page.enable");
await cdp("Runtime.enable");
await cdp("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});

await navigate("http://127.0.0.1:5173/");
await evaluate(`localStorage.removeItem("langtextflow:onboarding:v1"); location.reload(); true`);
await waitFor(() => evaluate(`document.querySelector('.onboarding-dialog') !== null`), "onboarding dialog");
await assertAccessible("onboarding");

await evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); location.reload(); true`);
await waitFor(() => evaluate(`document.querySelector('[data-testid="session-toggle"]') !== null`), "operator UI");
await assertAccessible("operator");

const startResponse = await fetch("http://127.0.0.1:8000/api/v1/session/start", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    engine: "mock",
    source_language: "ko",
    target_languages: ["en"],
    correction_provider: "none",
    translation_provider: "none"
  }),
});
if (!startResponse.ok) throw new Error(`a11y session start failed: ${startResponse.status} ${await startResponse.text()}`);
const state = await startResponse.json();
try {
  await navigate(`http://127.0.0.1:5173/audience/${state.join_code}`);
  await waitFor(() => evaluate(`document.querySelector('.audience-shell') !== null`), "audience UI");
  await assertAccessible("audience");
} finally {
  await fetch("http://127.0.0.1:8000/api/v1/session/stop", { method: "POST" });
}

socket.close();
console.log("axe accessibility smoke passed for onboarding, operator, and audience");
