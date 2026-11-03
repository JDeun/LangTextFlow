import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { AudienceApp } from "../../src/AudienceApp";
import { LocaleProvider } from "../../src/i18n";
import type { AudienceSessionView, TranscriptEvent } from "../../src/types";

// Only HTTP/WS are synthetic: render the actual audience, parser, socket hook and caption UI.
export async function runAudienceTargetChecks() {
  const assert = (condition: unknown, message: string) => {
    if (!condition) throw new Error(message);
  };
  const until = async (check: () => boolean, message: string) => {
    const deadline = performance.now() + 5000;
    while (!check() && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert(check(), message);
  };
  const originalFetch = window.fetch;
  const originalSocket = window.WebSocket;
  const sockets: FixtureSocket[] = [];
  class FixtureSocket {
    onopen: ((event: Event) => void) | null = null;
    onclose: ((event: CloseEvent) => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    closed = false;
    constructor(readonly url: string) { sockets.push(this); }
    open() { flushSync(() => this.onopen?.(new Event("open"))); }
    emit(payload: unknown) {
      flushSync(() => this.onmessage?.(new MessageEvent("message", {
        data: JSON.stringify(payload),
      })));
    }
    close(code = 1000) {
      this.closed = true;
      this.onclose?.(new CloseEvent("close", { code }));
    }
  }
  const session: AudienceSessionView = {
    session_id: "synthetic-session", join_code: "mixed-targets", running: true,
    title: "Synthetic audience acceptance", presenter: null, preset: "general",
    source_language: "en", target_languages: ["ko", "ja"], started_at: null,
    collect_audience_metrics: false,
    display_settings: {
      font_family: "system", font_scale_percent: 100, max_lines: 3,
      hold_seconds: 0, show_source_when_translated: true, text_align: "left",
    },
  };
  let metadataReads = 0;
  window.fetch = async (input, init) => {
    assert((init?.method ?? "GET") === "GET", "audience fixture attempted a server mutation");
    assert(new URL(String(input), location.href).pathname === "/api/v1/audience/mixed-targets",
      "audience fixture attempted an unexpected API call");
    metadataReads++;
    return Response.json(session);
  };
  window.WebSocket = FixtureSocket as unknown as typeof WebSocket;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const primary = () => host.querySelector(".audience-caption")?.textContent;
  const transcript = () => [...host.querySelectorAll(".audience-transcript article span")]
    .map(item => item.textContent);
  const choose = (language: string) => {
    const select = host.querySelector<HTMLSelectElement>(".audience-language select")!;
    select.value = language;
    flushSync(() => select.dispatchEvent(new Event("change", { bubbles: true })));
  };
  const source: TranscriptEvent = {
    type: "transcript", segment_id: "mixed-source", version: 1, source_revision: 1,
    stage: "stable", source_language: "en", text: "Grace is a gift.", original_text: null,
    translations: {}, translation_states: { ko: "pending", ja: "pending" },
    start_ms: 0, end_ms: 1000, speaker: null, confidence: null, correction: null,
    committed: false, emitted_at: new Date().toISOString(),
  };
  const japanese = "恵みは贈り物です。";
  try {
    flushSync(() => root.render(<LocaleProvider><AudienceApp
      joinCode="mixed-targets" displayMode={false} /></LocaleProvider>));
    await until(() => !!host.querySelector(".audience-language select") && sockets.length === 1,
      "actual audience session did not initialize");
    assert(new URL(sockets[0].url).pathname === "/ws/audience/mixed-targets",
      "audience socket used the wrong scope");
    sockets[0].open();
    sockets[0].emit({ type: "snapshot", segments: [] });
    sockets[0].emit(source);
    await until(() => primary() === "한국어 번역 중…", "KO pending state did not render");
    assert(transcript()[0] === "한국어 번역 중…", "pending history fell back to English");

    const mixed: TranscriptEvent = {
      ...source, version: 2, stage: "translated", translations: { ja: japanese },
      translation_states: { ko: "failed", ja: "available" },
    };
    sockets[0].emit(mixed);
    await until(() => primary() === "한국어 번역을 사용할 수 없습니다.",
      "KO failure was hidden by the successful JA target");
    assert(transcript()[0] === primary() && !host.querySelector(".audience-source"),
      "failed KO target displayed English source as translation");
    choose("ja");
    assert(primary() === japanese && transcript()[0] === japanese,
      "KO failure suppressed the successful JA caption");
    assert(host.querySelector(".audience-source")?.textContent === source.text,
      "JA caption lost its separate source line");
    choose("en");
    assert(primary() === source.text && transcript()[0] === source.text,
      "explicit English source selection lost the original caption");
    choose("ko");
    assert(primary() === "한국어 번역을 사용할 수 없습니다.",
      "changing back to failed KO reused English or Japanese text");
    choose("ja");
    sockets[0].emit({ ...mixed, version: 3, stage: "committed", committed: true });
    sockets[0].emit(source);
    assert(primary() === japanese, "old source revision overwrote a completed JA result");

    // A transient offline connection clears both caption surfaces before retry/snapshot.
    sockets[0].close(1006);
    await until(() => !!host.querySelector(".connection.offline")
      && primary() === "" && transcript().length === 0, "offline audience retained old captions");
    await until(() => sockets.length === 2, "real socket hook did not schedule reconnect");
    sockets[1].open();
    assert(primary() === "" && transcript().length === 0,
      "reconnection restored old captions before an authoritative snapshot");
    const restored: TranscriptEvent = {
      ...source, segment_id: "replacement-source", text: "Peace be with you.", version: 1,
      stage: "committed", committed: true, translations: { ko: "평안이 함께하시길.", ja: "平安を。" },
      translation_states: { ko: "available", ja: "available" },
    };
    sockets[1].emit({ type: "snapshot", segments: [restored] });
    await until(() => primary() === "平安を。", "fresh reconnect snapshot did not replace captions");
    sockets[0].emit({ ...mixed, version: 99, translations: { ja: "STALE SOCKET" } });
    sockets[0].close(1006);
    assert(primary() === "平安を。" && transcript().length === 1,
      "late retired socket changed or cleared the new snapshot");
    choose("ko");
    assert(primary() === "평안이 함께하시길." && transcript()[0] === primary(),
      "fresh EN→KO translation was replaced by unavailable or English fallback");
    assert(metadataReads === 1, "reconnect silently changed audience metadata ownership");
    return "actual AudienceApp: EN source, KO pending/failed, JA success, language changes, "
      + "offline clear, reconnect snapshot, retired socket rejection, restored EN→KO";
  } finally {
    flushSync(() => root.unmount());
    host.remove();
    window.fetch = originalFetch;
    window.WebSocket = originalSocket;
    assert(sockets.every(socket => socket.closed), "audience fixture leaked a socket after unmount");
  }
}
