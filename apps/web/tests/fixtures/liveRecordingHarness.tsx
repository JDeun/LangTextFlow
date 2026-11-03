import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { LiveRecordingControls } from "../../src/LiveRecordingControls";
import { PostEventPanel } from "../../src/PostEventPanel";
import { LIVE_RECORDING_COPY } from "../../src/liveRecordingCopy";
import type { SessionDetail, TranscriptEvent } from "../../src/types";

export async function runLiveRecordingChecks() {
  const t = LIVE_RECORDING_COPY.en;
  const assert = (condition: unknown, message: string) => { if (!condition) throw new Error(message); };
  const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
  const until = async (check: () => boolean, message: string) => {
    for (let i = 0; !check() && i < 100; i++) await tick();
    assert(check(), message);
  };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const originalFetch = window.fetch;
  const calls: { path: string; method: string; body: Record<string, unknown> }[] = [];
  const idle = (sid: string) => ({ session_id: sid, session_running: true, available: true,
    active: false, phase: "idle", error: null, start_offset_ms: null, recording: null });
  let current: Record<string, unknown> = idle("A");
  let heldGet: Response | null = null, heldPost: Response | null = null;
  let malformed = false;
  window.fetch = async (input, init) => {
    const path = new URL(String(input)).pathname, method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ path, method, body });
    if (method === "POST") {
      if (heldPost) { const response = heldPost; heldPost = null; return response; }
      current = path.endsWith("/start") ? { ...idle(body.expected_session_id), active: true, phase: "recording", start_offset_ms: 1000 }
        : { ...idle(body.expected_session_id), recording: { recording_id: "00000000-0000-0000-0000-000000000001" } };
      return Response.json(current);
    }
    if (heldGet) { const response = heldGet; heldGet = null; return response; }
    return Response.json(malformed ? { ...current, session_id: "foreign" } : current);
  };
  const render = (sid = "A", apiUrl = "http://127.0.0.1:8000") => flushSync(() => root.render(
    <LiveRecordingControls apiUrl={apiUrl} sessionId={sid} locale="en" />,
  ));
  const button = (label: string) => Array.from(host.querySelectorAll("button")).find(el => el.textContent === label)!;
  const deferredJson = () => {
    let resolve!: (value: unknown) => void;
    const promise = new Promise(value => { resolve = value; });
    const response = new Response(); Object.defineProperty(response, "json", { value: () => promise });
    return { response, resolve };
  };
  try {
    render();
    await until(() => !button(t.start).disabled, "initial recorder metadata unavailable");
    button(t.start).click();
    await until(() => !button(t.stop).disabled, "start did not leave independent stop available");
    assert(calls.at(-1)!.body.expected_session_id === "A" && host.textContent?.includes("1.000s"), "start lacked identity/PCM offset");
    button(t.stop).click();
    await until(() => !button(t.start).disabled, "stop did not allow a new recording");
    assert(host.querySelector<HTMLAnchorElement>("a[download]")!.href.includes("/history/A/recordings/00000000-0000-0000-0000-000000000001/audio"), "saved download used wrong session/asset");
    assert(calls.every(call => !call.path.endsWith("/session/start") && !call.path.endsWith("/session/stop")), "recorder action changed source session");
    const oldPoll = deferredJson(); heldGet = oldPoll.response;
    button(t.refresh).click(); await tick();
    button(t.start).click(); await until(() => !button(t.stop).disabled, "second recording did not start");
    oldPoll.resolve(idle("A")); await tick(); await tick();
    assert(!button(t.stop).disabled && button(t.start).disabled, "pre-action poll replaced new recorder state");

    // Deferred POST JSON from an older session/API is never installed or guessed-stopped.
    const oldPost = deferredJson(); heldPost = oldPost.response;
    button(t.stop).click(); await tick();
    current = idle("B"); render("B", "http://127.0.0.1:8002");
    await until(() => !button(t.start).disabled, "new session state did not load");
    oldPost.resolve({ ...idle("A"), active: true, phase: "recording", start_offset_ms: 9000 });
    await tick(); await tick();
    assert(!button(t.start).disabled && button(t.stop).disabled, "old session response overwrote new controls");
    assert(calls.filter(call => call.method === "POST").length === 4, "scope change guessed an extra remote stop");
    malformed = true; button(t.refresh).click(); await tick(); await tick();
    assert(button(t.start).disabled && button(t.stop).disabled && host.textContent?.includes(t.failure), "wrong-session metadata authorized controls");
    malformed = false; button(t.refresh).click(); await until(() => !button(t.start).disabled, "status refresh did not recover");

    // Actual PostEventPanel wiring: live citations subtract the selected file's
    // known offset; unknown imported/legacy recordings never imply zero.
    const segments = [
      { segment_id: "inside-A", start_ms: 1250 }, { segment_id: "gap", start_ms: 1600 },
      { segment_id: "inside-B", start_ms: 2000 },
    ].map(item => ({ ...item, committed: true })) as TranscriptEvent[];
    window.fetch = async input => String(input).endsWith("/transcripts") ? Response.json({ versions: [], approved_transcript_id: null }) : Response.json({
      recordings: [
        { recording_id: "A", duration_seconds: .5, size_bytes: 16044, start_offset_ms: 1000 },
        { recording_id: "B", duration_seconds: .75, size_bytes: 24044, start_offset_ms: 1750 },
        { recording_id: "legacy", duration_seconds: 100, size_bytes: 3200044 },
      ], summary: { summary_id: "summary", language: "en", provider: "ollama", model: "owned-fixture", status: "completed", content: "[inside-A at 1s] [gap at 1s] [inside-B at 2s]", review: null },
      summary_stale: false, summary_external_providers: ["openai-compatible"],
    });
    flushSync(() => root.render(<PostEventPanel apiUrl="http://127.0.0.1:8000" locale="en" session={{ session_id: "source", source_language: "ko" } as SessionDetail}
      segments={segments} onOpenSegment={() => {}} />));
    await until(() => host.querySelectorAll('[data-testid="session-audio-seek"]').length === 2, "known-span citation playback not wired");
    assert(!host.querySelector('[data-testid="session-audio-seek"][data-segment-id="gap"]') && host.textContent?.includes(t.unknown), "gap/legacy timing was invented");
    const player = Array.from(host.querySelectorAll("audio")).find(audio => audio.src.includes("/B/audio"))!;
    Object.defineProperty(player, "duration", { value: .75 }); Object.defineProperty(player, "readyState", { value: 1 });
    host.querySelector<HTMLButtonElement>('[data-testid="session-audio-seek"][data-segment-id="inside-B"]')!.click();
    await until(() => document.activeElement === player, "citation did not focus the correct recording");
    assert(Math.abs(player.currentTime - .25) < .001 && player.paused, "absolute 2s citation did not seek file-local .25s without autoplay");
    return "independent recorder actual React: start/stop/asset download, identity and offset, no source stop, stale poll/JSON/API/session fences, fail-closed recovery, correct live file-local seek and unknown gaps";
  } finally {
    flushSync(() => root.unmount()); host.remove(); window.fetch = originalFetch;
  }
}
