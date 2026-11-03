import { spawn, execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const scratch = await mkdtemp(join(tmpdir(), "ltf-independent-recorder-"));
const python = execFileSync("uv", ["run", "python", "-c", "import sys; print(sys.executable)"], { encoding: "utf8", cwd: root }).trim();
const env = { ...process.env, PYTHONPATH: [join(root, "apps/server"), join(root, "apps/server/tests")].join(delimiter),
  LANGTEXTFLOW_DATABASE_PATH: join(scratch, "events.db"), LANGTEXTFLOW_MODEL_CACHE_DIR: join(scratch, "models"),
  LANGTEXTFLOW_MDNS_ENABLED: "false", LANGTEXTFLOW_FRONTEND_PORT: "5176",
  LANGTEXTFLOW_CORS_ORIGINS: JSON.stringify(["http://127.0.0.1:5176"]),
  VITE_API_URL: "http://127.0.0.1:8002", HF_HUB_OFFLINE: "1" };
const children = [];
let target, socket, audio;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch { /* owned services may still be starting */ }
    await sleep(100);
  }
  throw new Error(`Independent recorder timed out: ${label}`);
}
function start(args, name, cwd = root) {
  const child = spawn(args[0], args.slice(1), { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  child.stdout.on("data", data => { log = (log + data).slice(-12000); });
  child.stderr.on("data", data => { log = (log + data).slice(-12000); });
  children.push({ child, name, log: () => log });
}
const api = "http://127.0.0.1:8002", web = "http://127.0.0.1:5176", chrome = "http://127.0.0.1:9222";
let id = 1;
const pending = new Map();
function cdp(method, params = {}) {
  return new Promise((resolve, reject) => {
    const token = id++; pending.set(token, { resolve, reject });
    socket.send(JSON.stringify({ id: token, method, params }));
  });
}
async function evaluate(expression) {
  const result = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function json(path, options) {
  const response = await fetch(api + path, options);
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return await response.json();
}
async function cropped(selector, name) {
  const document = await cdp("DOM.getDocument");
  const { nodeId } = await cdp("DOM.querySelector", { nodeId: document.root.nodeId, selector });
  await cdp("DOM.scrollIntoViewIfNeeded", { nodeId });
  await sleep(150); // Capture the settled compositor frame, not smooth-scroll motion.
  const { model } = await cdp("DOM.getBoxModel", { nodeId });
  const origin = await evaluate("({x:scrollX,y:scrollY})");
  let clip = { x: Math.max(0, model.border[0] + origin.x - 6), y: Math.max(0, model.border[1] + origin.y - 6),
    width: model.width + 12, height: model.height + 12, scale: 1 };
  const ordinary = await evaluate(`(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (node.closest('.control-panel')) return null;
    const r = node.getBoundingClientRect();
    return {x:r.left + scrollX - 6, y:r.top + scrollY - 6, width:r.width + 12, height:r.height + 12, scale:1};
  })()`);
  if (ordinary) clip = ordinary;
  const result = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, clip });
  await mkdir(join(root, "artifacts/e2e"), { recursive: true });
  await writeFile(join(root, `artifacts/e2e/${name}.png`), Buffer.from(result.data, "base64"));
}
try {
  start([python, "scripts/e2e_ollama_stub.py", join(scratch, "provider-port")], "provider");
  await until(() => readFile(join(scratch, "provider-port"), "utf8").then(Boolean), "owned provider");
  env.LANGTEXTFLOW_OLLAMA_URL = `http://127.0.0.1:${(await readFile(join(scratch, "provider-port"), "utf8")).trim()}`;
  start([python, "scripts/e2e_recording_stub.py"], "api");
  start([process.execPath, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5176", "--strictPort"], "web", join(root, "apps/web"));
  await until(() => fetch(api + "/health").then(r => r.ok), "owned API");
  await until(() => fetch(web).then(r => r.ok), "owned UI");
  const source = await json("/api/v1/session/start", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine: "faster-whisper", record_audio: false, translation_provider: "none", correction_provider: "none",
      context: { title: "Owned synthetic independent recorder browser fixture" } }) });
  audio = new WebSocket(`ws://127.0.0.1:8002/ws/audio?expected_session_id=${encodeURIComponent(source.session_id)}`);
  const configured = new Promise((resolve, reject) => {
    audio.addEventListener("message", event => { const m = JSON.parse(event.data); if (m.type === "audio_config") resolve(m); }, { once: true });
    audio.addEventListener("error", reject, { once: true });
  });
  await configured;
  let sentFrames = 0;
  async function send(count) {
    const pcm = Buffer.alloc(count * 4);
    for (let at = 0; at < count; at++) pcm.writeFloatLE(.25, at * 4);
    audio.send(pcm); sentFrames++;
    await until(async () => (await json("/api/v1/metrics")).audio_frames_received === sentFrames, "accepted PCM frame");
    if (audio.readyState !== WebSocket.OPEN) throw new Error("recorder action closed the original audio source socket");
  }
  await send(16000); // Captions run for 1s before explicit opt-in.
  target = await (await fetch(`${chrome}/json/new?${encodeURIComponent(web)}`, { method: "PUT" })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  socket.addEventListener("message", event => {
    const result = JSON.parse(event.data), waiter = pending.get(result.id);
    if (!waiter) return;
    pending.delete(result.id); if (result.error) waiter.reject(new Error(JSON.stringify(result.error))); else waiter.resolve(result.result);
  });
  await cdp("Page.enable"); await cdp("Runtime.enable");
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: "localStorage.setItem('langtextflow:onboarding:v1', 'complete')" });
  await cdp("Page.navigate", { url: web });
  await until(() => evaluate("document.querySelector('[data-testid=live-recording-controls]') !== null"), "hydrated actual operator controls");
  await evaluate(`(() => { const select = document.querySelector('[data-testid="locale-select"]'); select.value = 'en'; select.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  await evaluate("document.querySelector('.preflight-close')?.click()");
  await until(() => evaluate("document.querySelector('.preflight-panel') === null"), "dismissed preflight drawer");
  const panel = '[data-testid="live-recording-controls"]';
  const click = label => evaluate(`Array.from(document.querySelectorAll('${panel} button')).find(button => button.textContent === ${JSON.stringify(label)}).click()`);
  await until(() => evaluate(`Array.from(document.querySelectorAll('${panel} button')).some(button => button.textContent === 'Start recording only' && !button.disabled)`), "opt-out recorder ready");
  if ((await json("/api/v1/session/recording")).active) throw new Error("hydration silently opted into recording");
  await click("Start recording only");
  await until(async () => (await json("/api/v1/session/recording")).active, "late recording start");
  await send(4000); await send(4000);
  await click("Stop recording only");
  await until(async () => (await json("/api/v1/session/recording")).phase === "idle", "independent stop");
  const first = (await json("/api/v1/session/recording")).recording;
  if (first.start_offset_ms !== 1000 || first.duration_seconds !== .5 || !(await json("/api/v1/state")).running) throw new Error("first recording/continuing-caption clock mismatch");
  const liveDownload = await fetch(`${api}/api/v1/history/${source.session_id}/recordings/${first.recording_id}/audio`);
  if (!liveDownload.ok) throw new Error("completed recording unavailable while captions continue");
  await send(4000); // Explicit unrecorded .25s gap.
  await until(() => evaluate(`Array.from(document.querySelectorAll('${panel} button')).some(button => button.textContent === 'Start recording only' && !button.disabled)`), "recording resume ready");
  await click("Start recording only");
  await until(async () => (await json("/api/v1/session/recording")).active, "resumed recording");
  await until(() => evaluate(`Array.from(document.querySelectorAll('${panel} button')).some(button => button.textContent === 'Stop recording only' && !button.disabled)`), "resumed operator controls");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, mobile: false, deviceScaleFactor: 1 });
  await cropped(panel, "independent-recorder-controls-desktop-en");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, mobile: true, deviceScaleFactor: 1 });
  await cropped(panel, "independent-recorder-controls-mobile-en");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, mobile: false, deviceScaleFactor: 1 });
  await send(4000); await send(8000);
  await click("Stop recording only");
  await until(async () => (await json("/api/v1/session/recording")).phase === "idle", "second stop");
  const second = (await json("/api/v1/session/recording")).recording;
  if (second.start_offset_ms !== 1750 || second.duration_seconds !== .75 || audio.readyState !== WebSocket.OPEN) throw new Error("resumed recording offset/source continuity lost");
  for (const row of [first, second]) {
    const response = await fetch(`${api}/api/v1/history/${source.session_id}/recordings/${row.recording_id}/audio`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (!response.ok || createHash("sha256").update(bytes).digest("hex") !== row.sha256
      || bytes.readUInt32LE(40) !== row.duration_seconds * 16000 * 2 || bytes.readInt16LE(44) !== 8192) throw new Error("real WAV byte/hash/sample mismatch");
  }
  // Stop captions only after both independent recordings finished.
  await evaluate("document.querySelector('[data-testid=session-toggle]').click()");
  await until(async () => !(await json("/api/v1/state")).running, "source session explicit stop");
  await until(() => evaluate("document.querySelector('[data-testid=session-toggle].start-button') !== null"), "source stop UI committed");
  await evaluate("document.querySelector('.preflight-close')?.click()");
  await until(() => evaluate("document.querySelector('.preflight-panel') === null"), "dismissed post-stop preflight drawer");
  await json(`/api/v1/history/${source.session_id}/summary`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "ollama", model: "synthetic-e2e", language: "en" }) });
  await until(async () => (await json(`/api/v1/history/${source.session_id}/archive`)).summary?.status === "completed", "owned synthetic cited summary");
  await evaluate("document.querySelector('.history-heading button').click()");
  await until(() => evaluate("document.querySelector('.history-open') !== null"), "saved history");
  await evaluate("document.querySelector('.history-open').click()");
  await until(() => evaluate("document.querySelector('.history-post-summary textarea') !== null"), "source summary review editor");
  const originals = await json(`/api/v1/history/${source.session_id}/captions`);
  const reviewed = "Owned synthetic source review " + originals.filter(segment => segment.committed)
    .map(segment => `[${segment.segment_id} at ${Math.floor(segment.start_ms / 1000)}s]`).join(" ");
  await evaluate(`(() => { const field = document.querySelector('.history-post-summary textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(field, ${JSON.stringify(reviewed)}); field.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await until(() => evaluate(`document.querySelector('.history-post-summary textarea')?.value === ${JSON.stringify(reviewed)}`), "operator cited review text");
  await evaluate("Array.from(document.querySelectorAll('.history-post-summary button')).find(button => button.textContent === 'Save review draft').click()");
  await until(async () => !!(await json(`/api/v1/history/${source.session_id}/archive`)).summary?.review, "saved actual source review");
  await until(() => evaluate("document.querySelector('[data-testid=session-audio-seek][data-segment-id=clock-0006]') !== null"), "absolute source citation audio link");
  await evaluate("document.querySelector('[data-testid=session-audio-seek][data-segment-id=clock-0006]').click()");
  await until(() => evaluate(`(() => { const a = Array.from(document.querySelectorAll('audio')).find(item => item.src.includes(${JSON.stringify(second.recording_id)})); return a && a.readyState >= 1 && Math.abs(a.currentTime - .25) < .02 && a.paused && document.activeElement === a; })()`), "absolute 2s source seeks .25s in resumed WAV without autoplay");
  if (await evaluate("document.querySelector('[data-testid=session-audio-seek][data-segment-id=clock-0004]') !== null")) throw new Error("unrecorded gap acquired invented audio alignment");
  await cdp("Runtime.evaluate", { expression: await readFile(join(root, "apps/web/node_modules/axe-core/axe.min.js"), "utf8") });
  const violations = await evaluate("axe.run(document.querySelector('.history-detail'), {runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}).then(r => r.violations.map(v => v.id))");
  if (violations.length) throw new Error("independent recording accessibility: " + JSON.stringify(violations));
  const playback = `[data-testid="recording-player"][data-recording-id="${second.recording_id}"]`;
  await cropped(playback, "independent-recorder-playback-desktop-en");
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, mobile: true, deviceScaleFactor: 1 });
  await cropped(playback, "independent-recorder-playback-mobile-en");
  if (!await evaluate("document.documentElement.scrollWidth <= innerWidth + 1")) throw new Error("independent recording mobile overflow");
  console.log("independent recorder browser: real HTTP/audioWS/PCM/WAV, opt-out/reload, late start/stop/resume without source interruption, exact offsets/hashes, live download, 2s→resumed .25s citation, missing-gap rejection, mobile/axe; synthetic ASR only");
} catch (error) {
  console.error("Independent recorder failure:", error);
  for (const child of children) console.error(`${child.name}: ${child.log()}`);
  throw error;
} finally {
  audio?.close();
  if (target) await fetch(`${chrome}/json/close/${target.id}`).catch(() => {});
  socket?.close();
  const exits = children.map(({ child }) => child.exitCode !== null || child.signalCode !== null
    ? Promise.resolve() : new Promise(resolve => child.once("exit", resolve)));
  for (const { child } of children) if (child.exitCode === null && child.signalCode === null) child.kill();
  await Promise.all(children.map(async ({ child }, index) => {
    await Promise.race([exits[index], sleep(3000)]);
    if (child.exitCode === null && child.signalCode === null) {
      child.kill("SIGKILL"); await Promise.race([exits[index], sleep(3000)]);
    }
  }));
  await rm(scratch, { recursive: true, force: true });
}
