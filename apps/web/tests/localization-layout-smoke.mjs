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

async function reloadDocument() {
  await evaluate("window.__ltfLocaleReloadProbe = true");
  await cdp("Page.reload", { ignoreCache: true });
  await waitFor(async () => {
    try {
      return await evaluate("window.__ltfLocaleReloadProbe !== true");
    } catch {
      return false;
    }
  }, "fresh document after reload");
  await waitFor(() => evaluate(`!!document.querySelector('[data-testid="locale-select"]')`), "operator mount after reload");
}

async function setLocale(locale) {
  // Drive the real React language switcher instead of mutating storage behind
  // LocaleProvider's back. Direct writes were overwritten by its state effect
  // even when injected around a reload (observed locale/DOM/storage all "ko").
  const changed = await evaluate(`(() => {
    const select = document.querySelector('[data-testid="locale-select"]');
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLSelectElement.prototype, "value"
    )?.set;
    if (!select || !nativeSetter) return false;
    nativeSetter.call(select, ${JSON.stringify(locale)});
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
  if (!changed) throw new Error(`Locale selector not available for ${locale}`);

  const checkLocale = async () => {
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
  };
  const assertLocale = async (phase) => {
    await waitFor(checkLocale, `locale ${locale} (${phase})`).catch(async (error) => {
      const observed = await evaluate(`JSON.stringify({
        origin: location.origin,
        readyState: document.readyState,
        htmlLang: document.documentElement.lang,
        select: document.querySelector('[data-testid="locale-select"]')?.value ?? null,
        storedLocale: localStorage.getItem("langtextflow:locale:v1"),
      })`).catch((diagnosticError) => String(diagnosticError));
      throw new Error(`${error.message}; observed browser state: ${observed}`);
    });
  };

  await assertLocale("UI change");
  // Check persistence across reload only after React has written the new
  // locale. Page.reload is issued through CDP, not Runtime.evaluate.
  await reloadDocument();
  await assertLocale("reload persistence");
}

// CDP target creation can finish while its first page navigation or React
// bootstrap is still in flight. Do not change locale before the operator UI
// and its locale selector are mounted on the intended origin.
await cdp("Page.enable");
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

// Check the painted caption surface too: document overflow alone misses a
// wide aspect-ratio child clipped by its preview panel. Native consent inputs
// must also retain a square target when their adjacent copy wraps or grows.
for (const locale of Object.keys(stressSamples)) {
  await setLocale(locale);
  for (const [width, textScale] of [[320, 100], [390, 100], [720, 100], [861, 100], [1024, 100], [1440, 100], [1440, 200]]) {
    await cdp("Emulation.setDeviceMetricsOverride", {
      width, height: 1000, deviceScaleFactor: 1, mobile: width < 500,
    });
    await evaluate(`document.documentElement.style.fontSize = "${textScale}%"`);
    await waitFor(() => evaluate(`Math.abs(parseFloat(document.querySelector('.app-shell').style.getPropertyValue('--ltf-operator-header-height'))
      - document.querySelector('.topbar').getBoundingClientRect().height) < 1`), "header resize observation");
    await evaluate(`document.querySelector('.control-panel').scrollTop = 0;
      window.scrollTo(0, 300); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    const geometry = await evaluate(`(() => {
      const preview = document.querySelector('.preview').getBoundingClientRect();
      const stage = document.querySelector('.stage-screen').getBoundingClientRect();
      const brand = document.querySelector('.brand');
      const panel = document.querySelector('.control-panel');
      const heading = panel.querySelector('.section-heading');
      const box = heading.getBoundingClientRect();
      const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      const inputs = [...document.querySelectorAll('input[type="checkbox"]')]
        .filter(input => input.getClientRects().length);
      return {
        fits: document.documentElement.scrollWidth <= innerWidth + 1
          && stage.left >= preview.left && stage.right <= preview.right + 1,
        brandFits: brand.getBoundingClientRect().height
          <= parseFloat(getComputedStyle(brand).lineHeight) + 1,
        controlsClear: getComputedStyle(panel).position !== 'sticky'
          || (panel.getBoundingClientRect().top >= document.querySelector('.topbar').getBoundingClientRect().bottom
            && heading.contains(hit)),
        scroll: { y: scrollY, inner: panel.scrollTop, panel: panel.getBoundingClientRect().top,
          header: document.querySelector('.topbar').getBoundingClientRect().bottom, heading: box.top, hit: hit?.className },
        squareInputs: inputs.length > 0 && inputs.every(input => {
          const { width, height } = input.getBoundingClientRect();
          return width >= 16 && Math.abs(width - height) <= 1;
        }),
      };
    })()`);
    if (!geometry.fits || !geometry.squareInputs || !geometry.brandFits || !geometry.controlsClear) {
      throw new Error(`Caption/consent layout failed at ${width}px / ${textScale}% text: ${JSON.stringify(geometry)}`);
    }
    if (width >= 861) {
      await evaluate(`document.querySelector('.preflight-close')?.click()`);
      for (const [index, selector] of [[2, '.history > .section-heading'], [1, '.control-panel > .section-heading']]) {
        await evaluate(`document.querySelectorAll('.product-nav button')[${index}].click()`);
        let lastY;
        let stable = 0;
        await waitFor(async () => {
          const y = await evaluate("scrollY");
          stable = y === lastY ? stable + 1 : 0;
          lastY = y;
          return stable >= 2;
        }, "navigation scroll settled");
        const clear = await evaluate(`(() => {
          const heading = document.querySelector(${JSON.stringify(selector)});
          const r = heading.getBoundingClientRect();
          return r.top >= document.querySelector('.topbar').getBoundingClientRect().bottom
            && heading.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
        })()`);
        if (!clear) throw new Error(`Navigation hid ${selector} at ${width}px / ${textScale}% text`);
      }
    }
  }
}

// Without measurement support the header stays in document flow, so an
// independently sticky sidebar still cannot disappear behind it.
const fallback = await cdp("Page.addScriptToEvaluateOnNewDocument", { source: "window.ResizeObserver = undefined;" });
try {
  await reloadDocument();
  if (await evaluate("typeof ResizeObserver") !== "undefined") throw new Error("ResizeObserver fallback fixture was not installed");
  for (const [width, textScale] of [[861, 100], [1024, 100], [1440, 200]]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    await evaluate(`document.documentElement.style.fontSize = "${textScale}%";
      document.querySelector('.control-panel').scrollTop = 0;
      window.scrollTo(0, 300); new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    const clear = await evaluate(`(() => {
      const header = document.querySelector('.topbar');
      const heading = document.querySelector('.control-panel .section-heading');
      const r = heading.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return {
        clear: getComputedStyle(header).position !== 'sticky' && heading.contains(hit),
        headerPosition: getComputedStyle(header).position,
        headingTop: r.top, hit: hit?.className, observer: typeof ResizeObserver, scrollY,
      };
    })()`);
    if (!clear.clear) throw new Error(`Unmeasured header hid controls at ${width}px / ${textScale}% text: ${JSON.stringify(clear)}`);
  }
} finally {
  await cdp("Page.removeScriptToEvaluateOnNewDocument", { identifier: fallback.identifier });
  await reloadDocument();
}
await evaluate(`document.documentElement.style.fontSize = ""`);
const consentBefore = await evaluate(`(() => {
  const input = document.querySelector('[data-testid="operator-metrics-consent"]');
  input.focus();
  return input.checked;
})()`);
await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
if (await evaluate(`document.querySelector('[data-testid="operator-metrics-consent"]').checked`) === consentBefore) {
  throw new Error("Native consent checkbox did not toggle with Space");
}

socket.close();
console.log("localization layout smoke passed for en/ko/ja, narrow captions, zoom, and keyboard consent");
