import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

// The product E2E uses Vite preview; this actual-hook fixture needs a separate dev server.
const origin = process.env.LTF_WEB_URL || "http://127.0.0.1:5174";
const chrome = process.env.LTF_CDP_URL || "http://127.0.0.1:9222";
const page = `${origin}/tests/fixtures/operator-session.html`;
let devServer;
let serverOutput = "";
let target;
let socket;
try {
  if (!process.env.LTF_WEB_URL) {
    devServer = spawn(process.execPath, [
      fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
      "--host", "127.0.0.1", "--port", "5174", "--strictPort",
    ], { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: ["ignore", "pipe", "pipe"] });
    const collect = (chunk) => { serverOutput = (serverOutput + chunk).slice(-8000); };
    devServer.stdout.on("data", collect);
    devServer.stderr.on("data", collect);
    const deadline = Date.now() + 20_000;
    let ready = false;
    while (Date.now() < deadline && devServer.exitCode === null) {
      if (serverOutput.replace(/\u001b\[[0-9;]*m/g, "").includes(origin)
        && await fetch(page).then((response) => response.ok).catch(() => false)) {
        ready = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!ready) throw new Error(`Operator fixture Vite did not start: ${serverOutput}`);
  }
  const targetResponse = await fetch(`${chrome}/json/new?${encodeURIComponent(page)}`, { method: "PUT" });
  if (!targetResponse.ok) throw new Error(`CDP target creation failed: ${targetResponse.status}`);
  target = await targetResponse.json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.method === "Network.responseReceived" && message.params.response.status >= 400) {
      console.error(`Fixture HTTP ${message.params.response.status}: ${message.params.response.url}`);
    }
    if (message.method === "Runtime.exceptionThrown") {
      console.error(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    }
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
  await cdp("Page.enable");
  await cdp("Network.enable");
  await cdp("Runtime.enable");
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
  const historyResult = await cdp("Runtime.evaluate", {
    expression: `import('/tests/fixtures/historySelectionHarness.tsx').then((test) => test.runHistorySelectionChecks())`,
    awaitPromise: true, returnByValue: true,
  });
  if (historyResult.exceptionDetails) throw new Error(
    historyResult.exceptionDetails.exception?.description || historyResult.exceptionDetails.text,
  );
  console.log(`history selection smoke passed: ${historyResult.result.value}`);
  const summaryResult = await cdp("Runtime.evaluate", {
    expression: `import('/tests/fixtures/summaryPolicyHarness.tsx').then((test) => test.runSummaryPolicyChecks())`,
    awaitPromise: true, returnByValue: true,
  });
  if (summaryResult.exceptionDetails) throw new Error(
    summaryResult.exceptionDetails.exception?.description || summaryResult.exceptionDetails.text,
  );
  console.log(`summary transmission policy smoke passed: ${summaryResult.result.value}`);
  const audioResult = await cdp("Runtime.evaluate", {
    expression: `import('/tests/fixtures/audioCaptureHarness.ts').then((test) => test.runAudioCaptureChecks())`,
    awaitPromise: true, returnByValue: true,
  });
  if (audioResult.exceptionDetails) throw new Error(
    audioResult.exceptionDetails.exception?.description || audioResult.exceptionDetails.text,
  );
  console.log(`audio capture ownership smoke passed: ${audioResult.result.value}`);
} catch (error) {
  if (serverOutput) console.error(serverOutput);
  throw error;
} finally {
  if (target) await fetch(`${chrome}/json/close/${target.id}`).catch(() => undefined);
  socket?.close();
  if (devServer && devServer.exitCode === null) {
    const exited = new Promise((resolve) => devServer.once("exit", resolve));
    devServer.kill();
    await exited;
  }
}
