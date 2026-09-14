const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, label, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await check()) return;
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
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
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "evaluation failed");
  return result.result?.value;
}

async function setLocale(locale) {
  await evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); localStorage.setItem("langtextflow:locale:v1", ${JSON.stringify(locale)}); location.reload(); true`);
  await waitFor(
    () => evaluate(`document.readyState === "complete" && document.querySelector('[data-testid="locale-select"]')?.value === ${JSON.stringify(locale)}`),
    `locale ${locale}`,
  );
}

const stressSamples = {
  en: "ExtremelyLongLocalizedInterfaceStringWithoutNaturalBreaksForLayoutResilience".repeat(5),
  ko: "초장문현지화레이아웃검증문자열줄바꿈없는상태".repeat(8),
  ja: "非常に長いローカライズ済みインターフェース文字列レイアウト検証".repeat(8),
};

for (const [locale, stress] of Object.entries(stressSamples)) {
  await setLocale(locale);
  const fits = await evaluate(`(() => {
    const targets = [
      document.querySelector('.brand'),
      document.querySelector('.subtitle'),
      document.querySelector('.section-heading span'),
      document.querySelector('[data-testid="session-toggle"]'),
    ].filter(Boolean);
    const originals = targets.map((node) => node.textContent);
    targets.forEach((node) => { node.textContent = ${JSON.stringify(stress)}; });
    const noHorizontalOverflow = document.documentElement.scrollWidth <= window.innerWidth + 1;
    const controlsStayInside = targets.every((node) => node.getBoundingClientRect().right <= window.innerWidth + 1);
    targets.forEach((node, index) => { node.textContent = originals[index]; });
    return noHorizontalOverflow && controlsStayInside;
  })()`);
  if (!fits) throw new Error(`${locale} long-string localization stress caused horizontal overflow`);
}

socket.close();
console.log("localization layout smoke passed for en/ko/ja long-string stress");
