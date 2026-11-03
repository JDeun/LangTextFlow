// Requires the existing browser-smoke Chrome (9222) and Vite (5173), but no backend/models.
const origin = process.env.LTF_WEB_URL || "http://127.0.0.1:5173";
const chrome = process.env.LTF_CDP_URL || "http://127.0.0.1:9222";
const page = `${origin}/tests/fixtures/operator-session.html`;
const targetResponse = await fetch(`${chrome}/json/new?${encodeURIComponent(page)}`, { method: "PUT" });
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
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});
function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
try {
  await cdp("Page.enable");
  // Navigate again after enabling lifecycle events to avoid the new-target load race.
  const loaded = new Promise((resolve) => socket.addEventListener("message", function onLoad(event) {
    if (JSON.parse(event.data).method !== "Page.loadEventFired") return;
    socket.removeEventListener("message", onLoad);
    resolve();
  }));
  await cdp("Page.navigate", { url: page });
  await loaded;
  const result = await cdp("Runtime.evaluate", {
    expression: `import('/tests/fixtures/operatorSessionHarness.tsx').then((test) => test.runOperatorSessionChecks())`,
    awaitPromise: true, returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(
    result.exceptionDetails.exception?.description || result.exceptionDetails.text,
  );
  console.log(`operator session smoke passed: ${result.result.value}`);
} finally {
  await fetch(`${chrome}/json/close/${target.id}`).catch(() => undefined);
  socket.close();
}
