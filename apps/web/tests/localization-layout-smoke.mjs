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

let localePreloadScriptId = null;

async function setLocale(locale) {
  // Inject storage before React runs in the reloaded document. An immediate
  // write on the old page can race LocaleProvider's startup effect and be
  // overwritten with the old locale before Page.reload takes effect.
  if (localePreloadScriptId) {
    await cdp("Page.removeScriptToEvaluateOnNewDocument", {
      identifier: localePreloadScriptId,
    });
  }
  const preload = await cdp("Page.addScriptToEvaluateOnNewDocument", {
    source: `try {
      localStorage.setItem("langtextflow:onboarding:v1", "complete");
      localStorage.setItem("langtextflow:locale:v1", ${JSON.stringify(locale)});
    } catch (_) { /* Non-app initial contexts may have no storage. */ }`,
  });
  localePreloadScriptId = preload.identifier;
  // Navigate through Page rather than Runtime.evaluate; no destroyed-context
  // response is awaited as part of the navigation itself.
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor(async () => {
    try {
      return await evaluate(`document.readyState === "complete"
        && document.documentElement.lang === ${JSON.stringify(locale)}
        && document.querySelector('[data-testid="locale-select"]')?.value === ${JSON.stringify(locale)}
        && localStorage.getItem("langtextflow:locale:v1") === ${JSON.stringify(locale)}`);
    } catch (error) {
      if (/Inspected target navigated or closed|Execution context was destroyed|Cannot find context with specified id/i.test(String(error))) {
        return false;
      }
      throw error;
    }
  }, `locale ${locale}`).catch(async (error) => {
    const observed = await evaluate(`JSON.stringify({
      origin: location.origin,
      pathname: location.pathname,
      readyState: document.readyState,
      htmlLang: document.documentElement.lang,
      select: document.querySelector('[data-testid="locale-select"]')?.value ?? null,
      storedLocale: localStorage.getItem("langtextflow:locale:v1"),
      onboardingState: localStorage.getItem("langtextflow:onboarding:v1"),
      appPresent: !!document.querySelector("#root"),
      rootText: document.querySelector("#root")?.textContent?.slice(0, 160),
    })`).catch((diagnosticError) => String(diagnosticError));
    throw new Error(`${error.message}; observed browser state: ${observed}`);
  });
}

// CDP target creation can finish while its first page navigation or React
// bootstrap is still in flight. Do not change locale before the operator UI
// and its locale selector are mounted on the intended origin.
await waitFor(async () => {
  try {
    return await evaluate(`location.origin === "http://127.0.0.1:5173"
      && document.readyState === "complete"
      && !!document.querySelector('[data-testid="locale-select"]')`);
  } catch (error) {
    if (/Inspected target navigated or closed|Execution context was destroyed|Cannot find context with specified id/i.test(String(error))) {
      return false;
    }
    throw error;
  }
}, "initial operator mount", 30_000);

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
