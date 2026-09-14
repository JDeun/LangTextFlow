const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(check, label, timeoutMs = 10_000) {
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

async function key(key, modifiers = 0) {
  await cdp("Input.dispatchKeyEvent", {
    type: "keyDown",
    key,
    code: key,
    modifiers,
    windowsVirtualKeyCode: key === "Tab" ? 9 : 0,
    nativeVirtualKeyCode: key === "Tab" ? 9 : 0,
  });
  await cdp("Input.dispatchKeyEvent", {
    type: "keyUp",
    key,
    code: key,
    modifiers,
    windowsVirtualKeyCode: key === "Tab" ? 9 : 0,
    nativeVirtualKeyCode: key === "Tab" ? 9 : 0,
  });
}

await cdp("Page.enable");
await cdp("Runtime.enable");
await waitFor(() => evaluate("document.readyState === 'complete'"), "operator page");
await evaluate(`localStorage.setItem("langtextflow:onboarding:v1", "complete"); location.reload(); true`);
await waitFor(
  () => evaluate(`document.querySelector('.setup-button') !== null && document.querySelector('.onboarding-dialog') === null`),
  "operator setup button",
);

const opened = await evaluate(`(() => {
  const button = document.querySelector('.setup-button');
  if (!button) return false;
  button.focus();
  button.click();
  return true;
})()`);
if (!opened) throw new Error("setup button could not open onboarding dialog");

await waitFor(
  () => evaluate(`document.querySelector('.onboarding-dialog')?.contains(document.activeElement) === true`),
  "focus inside onboarding dialog",
);

const startsOnFirst = await evaluate(`(() => {
  const dialog = document.querySelector('.onboarding-dialog');
  if (!dialog) return false;
  const selector = "button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
  const focusable = Array.from(dialog.querySelectorAll(selector));
  return focusable.length > 1 && document.activeElement === focusable[0];
})()`);
if (!startsOnFirst) throw new Error("modal did not focus its first interactive control");

await key("Tab", 8); // Shift+Tab from the first element must wrap to the last.
const wrappedBackwards = await evaluate(`(() => {
  const dialog = document.querySelector('.onboarding-dialog');
  if (!dialog) return false;
  const selector = "button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
  const focusable = Array.from(dialog.querySelectorAll(selector));
  return focusable.length > 1 && document.activeElement === focusable[focusable.length - 1];
})()`);
if (!wrappedBackwards) throw new Error("Shift+Tab escaped the modal focus boundary");

const closed = await evaluate(`(() => {
  const close = document.querySelector('.onboarding-header button');
  if (!close) return false;
  close.click();
  return true;
})()`);
if (!closed) throw new Error("onboarding close button not found");
await waitFor(
  () => evaluate(`document.querySelector('.onboarding-dialog') === null`),
  "onboarding close",
);
await waitFor(
  () => evaluate(`document.activeElement?.classList.contains('setup-button') === true`),
  "focus restoration to setup button",
);

socket.close();
console.log("focus smoke passed: modal entry, trap and restoration");
