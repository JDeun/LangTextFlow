import { AudioCaptureController } from "../../src/audioCapture";

const assert = (condition: unknown, message: string) => {
  if (!condition) throw new Error(message);
};
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));
async function until(check: () => boolean, message: string) {
  for (let i = 0; !check() && i < 100; i++) await tick();
  assert(check(), message);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  void promise.catch(() => undefined);
  return { promise, resolve, reject };
}

class Track extends EventTarget {
  readyState = "live";
  stop() { this.readyState = "ended"; }
  end() { this.readyState = "ended"; this.dispatchEvent(new Event("ended")); }
}
class Stream {
  track = new Track();
  getTracks() { return [this.track]; }
  getAudioTracks() { return [this.track]; }
}
class AudioNode {
  disconnected = false;
  connect(_node: unknown) { /* The controller still owns the actual graph operations. */ }
  disconnect() { this.disconnected = true; }
}

async function withFakes(run: (env: any) => Promise<void>) {
  const env = {
    streams: [] as Stream[], sockets: [] as FakeSocket[], contexts: [] as FakeContext[],
    worklets: [] as FakeWorklet[], controllers: [] as AudioCaptureController[],
    permission: [] as Promise<Stream>[], enumeration: [] as Promise<unknown[]>[],
    module: [] as Promise<void>[], resume: [] as Promise<void>[], close: [] as Promise<void>[],
    acknowledgements: [] as Promise<void>[], gates: [] as ReturnType<typeof deferred<any>>[],
    permissionCalls: 0, enumerationCalls: 0, fatal: [] as string[],
    media: new EventTarget(),
    stream() { const stream = new Stream(); this.streams.push(stream); return stream; },
    gate<T>() { const gate = deferred<T>(); this.gates.push(gate); return gate; },
    controller() {
      const controller = new AudioCaptureController(message => this.fatal.push(message));
      this.controllers.push(controller);
      return controller;
    },
  };
  class FakeSocket {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    readyState = 0;
    bufferedAmount = 0;
    binaryType = "";
    sent: unknown[] = [];
    onmessage: ((event: any) => void) | null = null;
    onerror: ((event: any) => void) | null = null;
    onclose: ((event: any) => void) | null = null;
    constructor(readonly url: string) {
      env.sockets.push(this);
      void (env.acknowledgements.shift() || Promise.resolve()).then(() => {
        if (this.readyState === FakeSocket.CLOSED) return;
        this.readyState = FakeSocket.OPEN;
        this.onmessage?.({ data: JSON.stringify({
          type: "audio_config", required: true, sample_rate: 16000, engine: "fixture",
          channels: 1, sample_format: "f32le",
        }) });
      }).catch(() => undefined);
    }
    send(value: unknown) {
      assert(this.readyState === FakeSocket.OPEN, "send used a closed audio socket");
      this.sent.push(value);
    }
    close(code = 1000, reason = "") {
      this.readyState = FakeSocket.CLOSED;
      queueMicrotask(() => this.onclose?.({ code, reason }));
    }
  }
  class FakeContext {
    sampleRate: number;
    state = "suspended";
    destination = {};
    failNextSource = false;
    sources: AudioNode[] = [];
    audioWorklet = { addModule: async (_path: string) => {
      await (env.module.shift() || Promise.resolve());
    } };
    constructor(options: { sampleRate: number }) {
      this.sampleRate = options.sampleRate;
      env.contexts.push(this);
    }
    async resume() {
      await (env.resume.shift() || Promise.resolve());
      if (this.state !== "closed") this.state = "running";
    }
    async close() {
      this.state = "closed";
      await (env.close.shift() || Promise.resolve());
    }
    createMediaStreamSource(_stream: Stream) {
      if (this.failNextSource || this.state === "closed") throw new Error("fixture graph rejected stream");
      const node = new AudioNode(); this.sources.push(node); return node;
    }
    createGain() { return Object.assign(new AudioNode(), { gain: { value: 1 } }); }
  }
  class FakeWorklet extends AudioNode {
    port = { onmessage: null as ((event: any) => void) | null };
    constructor(_context: FakeContext, _name: string, _options: unknown) {
      super(); env.worklets.push(this);
    }
  }
  Object.assign(env.media, {
    getUserMedia: async () => {
      env.permissionCalls++;
      return await (env.permission.shift() || Promise.resolve(env.stream()));
    },
    enumerateDevices: async () => {
      env.enumerationCalls++;
      return await (env.enumeration.shift()
        || Promise.resolve([{ kind: "audioinput", deviceId: "default" }]));
    },
  });
  const restores: (() => void)[] = [];
  const replace = (target: object, key: string, value: unknown) => {
    const original = Object.getOwnPropertyDescriptor(target, key);
    Object.defineProperty(target, key, { value, configurable: true, writable: true });
    restores.push(() => original ? Object.defineProperty(target, key, original) : Reflect.deleteProperty(target, key));
  };
  replace(navigator, "mediaDevices", env.media);
  replace(window, "WebSocket", FakeSocket);
  replace(window, "AudioContext", FakeContext);
  replace(window, "AudioWorkletNode", FakeWorklet);
  try {
    await run(env);
    for (const controller of env.controllers) await controller.stop();
    assert(env.streams.every(stream => stream.track.readyState === "ended"), "completed controller case leaked a microphone");
    assert(env.contexts.every(context => context.state === "closed"), "completed controller case leaked an audio context");
    assert(env.sockets.every(socket => socket.readyState === FakeSocket.CLOSED), "completed controller case leaked a socket");
  } finally {
    env.gates.forEach(gate => gate.reject(new Error("fixture cleanup")));
    for (const controller of env.controllers) await controller.stop().catch(() => undefined);
    env.streams.forEach(stream => stream.getTracks().forEach(track => track.stop()));
    restores.reverse().forEach(restore => restore());
  }
}

const observe = (pending: Promise<number>) => pending.then(
  value => ({ value, error: null }), error => ({ value: null, error }),
);
function owner(env: any) {
  return { stream: env.streams.at(-1), socket: env.sockets.at(-1), worklet: env.worklets.at(-1) };
}
function assertCurrent(env: any, current = owner(env)) {
  assert(current.stream.track.readyState === "live", "stale work stopped the current microphone");
  assert(current.socket.readyState === 1, "stale work closed the current socket");
  const before = current.socket.sent.length;
  current.worklet.port.onmessage({ data: new ArrayBuffer(16) });
  assert(current.socket.sent.length === before + 1, "current audio stopped forwarding");
  assert(env.fatal.length === 0, "stale work invoked the current fatal callback");
}

export async function runAudioCaptureChecks() {
  // These are actual controller methods, with only browser platform APIs replaced.
  await withFakes(async env => {
    const permission = env.gate<Stream>(); env.permission.push(permission.promise);
    const controller = env.controller();
    const pending = observe(controller.start(undefined, "stopped-permission"));
    await until(() => env.permissionCalls === 1, "permission request did not start");
    await controller.stop();
    const late = env.stream(); permission.resolve(late);
    assert((await pending).error, "cancelled microphone startup completed successfully");
    assert(late.track.readyState === "ended", "late permission reopened a stopped microphone");
    assert(env.sockets.length === 0, "late permission opened a socket after stop");
  });

  for (const reject of [false, true]) await withFakes(async env => {
    const permission = env.gate<Stream>(); env.permission.push(permission.promise);
    const controller = env.controller();
    const pending = observe(controller.start());
    await until(() => env.permissionCalls === 1, "old permission did not start");
    await controller.stop(); await controller.start();
    const current = owner(env);
    let late: Stream | undefined;
    if (reject) permission.reject(new Error("old permission rejected"));
    else { late = env.stream(); permission.resolve(late); }
    assert((await pending).error, "superseded permission startup completed");
    if (late) assert(late.track.readyState === "ended", "late old permission retained its track");
    assert(env.sockets.length === 1, "old permission constructed another socket");
    assertCurrent(env, current);
  });

  await withFakes(async env => {
    const acknowledgement = env.gate<void>(); env.acknowledgements.push(acknowledgement.promise);
    const controller = env.controller();
    let settled = false;
    const pending = observe(controller.start()).then(result => { settled = true; return result; });
    await until(() => env.sockets.length === 1, "pending socket handshake did not start");
    await controller.stop();
    await until(() => settled, "stopped handshake still awaited a server acknowledgement");
    assert((await pending).error, "closed handshake startup completed");
    await controller.start(); const current = owner(env);
    acknowledgement.resolve(); await tick(); assertCurrent(env, current);
  });

  for (const boundary of ["module", "resume"] as const) {
    for (const reject of [false, true]) await withFakes(async env => {
      const gate = env.gate<void>(); env[boundary].push(gate.promise);
      const controller = env.controller();
      const old = observe(controller.start(undefined, "old-owner"));
      await until(() => env.contexts.length === 1 && env[boundary].length === 0,
        `${boundary} boundary was not reached`);
      if (reject) await controller.stop();
      assert(await controller.start(undefined, "new-owner") === 16000, "replacement start failed");
      if (reject) gate.reject(new Error(`old ${boundary} failure`)); else gate.resolve();
      assert((await old).error, `old ${boundary} startup incorrectly completed`);
      assertCurrent(env);
    });
  }

  await withFakes(async env => {
    const controller = env.controller(); await controller.start();
    const closing = env.gate<void>(); env.close.push(closing.promise);
    const oldWorklet = env.worklets[0].port.onmessage;
    const oldSocketError = env.sockets[0].onerror;
    const stopping = controller.stop();
    await controller.start(undefined, "new-during-close");
    closing.resolve(); await stopping;
    const current = env.sockets.at(-1);
    const sent = current.sent.length;
    oldWorklet({ data: new ArrayBuffer(16) }); oldSocketError(new Event("error"));
    assert(current.sent.length === sent, "old worklet forwarded PCM into the new socket");
    assertCurrent(env);
  });

  await withFakes(async env => {
    const controller = env.controller(); await controller.start();
    const enumeration = env.gate<unknown[]>(); env.enumeration.push(enumeration.promise);
    env.streams[0].track.end();
    await until(() => env.enumerationCalls === 1, "recovery enumeration did not start");
    await controller.stop(); await controller.start();
    const newer = env.gate<unknown[]>(); env.enumeration.push(newer.promise);
    env.streams.at(-1).track.end();
    await until(() => env.enumerationCalls === 2, "new owner recovery did not start");
    enumeration.resolve([{ kind: "audioinput", deviceId: "default" }]); await tick();
    env.media.dispatchEvent(new Event("devicechange")); await tick();
    assert(env.enumerationCalls === 2, "old finally reset the new recovery guard");
    newer.resolve([{ kind: "audioinput", deviceId: "default" }]);
    await until(() => env.permissionCalls === 3, "current recovery did not request replacement");
    await tick(); assertCurrent(env);
  });

  for (const restart of [false, true]) await withFakes(async env => {
    const controller = env.controller(); await controller.start();
    const permission = env.gate<Stream>(); env.permission.push(permission.promise);
    env.streams[0].track.end();
    await until(() => env.permissionCalls === 2, "recovery permission did not start");
    await controller.stop();
    if (restart) await controller.start();
    const current = restart ? owner(env) : null;
    const late = env.stream(); permission.resolve(late); await tick(); await tick();
    assert(late.track.readyState === "ended", "stale recovery leaked an unowned microphone track");
    if (current) assertCurrent(env, current);
    else assert(env.fatal.length === 0, "stopped recovery reported a current failure");
  });

  await withFakes(async env => {
    const controller = env.controller(); await controller.start();
    const resume = env.gate<void>(); env.resume.push(resume.promise);
    env.contexts[0].state = "suspended";
    window.dispatchEvent(new Event("focus"));
    await until(() => env.resume.length === 0, "focus resume did not start");
    await controller.stop(); await controller.start();
    resume.reject(new Error("old resume failure")); await tick(); assertCurrent(env);
  });

  await withFakes(async env => {
    const controller = env.controller(); await controller.start();
    env.contexts[0].failNextSource = true;
    env.streams[0].track.end();
    await until(() => env.permissionCalls === 2 && env.fatal.length === 1,
      "replacement graph failure did not settle");
    assert(env.streams[1].track.readyState === "ended", "failed graph leaked replacement microphone");
  });

  await withFakes(async env => {
    const controller = env.controller(); assert(await controller.start() === 16000, "normal start failed");
    assertCurrent(env);
    env.streams[0].track.end();
    await until(() => env.permissionCalls === 2, "normal recovery did not replace microphone");
    await tick(); assertCurrent(env);
    await controller.stop();
    assert(env.streams.every((stream: Stream) => stream.track.readyState === "ended"), "stop retained a live track");
    assert(env.contexts.every((context: {state: string}) => context.state === "closed"), "stop retained an audio context");
  });
  return "15 actual-controller cases: deferred permission/handshake/WebAudio/recovery, stale callbacks, stop ownership and PCM flow";
}
