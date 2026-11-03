import { getWebSocketUrl } from "./api";
import {
  AUDIO_BACKPRESSURE_CLOSE_CODE,
  AUDIO_PROTOCOL_ERROR_CODE,
  audioSocketPath,
  canQueueAudioFrame,
  chooseRecoveryDevice,
  microphoneErrorMessage,
  parseAudioSocketConfig,
} from "./audioCapturePolicy";
import type { AudioSocketConfig } from "./audioCapturePolicy";

export interface AudioInputDevice {
  deviceId: string;
  label: string;
}

export async function requestAudioInputs(): Promise<AudioInputDevice[]> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("이 환경에서는 마이크 입력을 사용할 수 없습니다.");
  }
  try {
    const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    permissionStream.getTracks().forEach((track) => track.stop());
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter((device) => device.kind === "audioinput")
      .map((device, index) => ({
        deviceId: device.deviceId,
        label: device.label || `오디오 입력 ${index + 1}`,
      }));
  } catch (error) {
    throw new Error(microphoneErrorMessage(error));
  }
}

function waitForAudioConfig(socket: WebSocket): Promise<AudioSocketConfig> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error("오디오 서버 응답이 없습니다.")), 5000);
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const payload = parseAudioSocketConfig(event.data);
        window.clearTimeout(timeout);
        resolve(payload);
      } catch (error) {
        window.clearTimeout(timeout);
        socket.close(AUDIO_PROTOCOL_ERROR_CODE, "invalid audio configuration");
        reject(error);
      }
    };
    socket.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error("오디오 스트림 서버에 연결할 수 없습니다."));
    };
    socket.onclose = (event) => {
      window.clearTimeout(timeout);
      reject(new Error(event.reason || "오디오 스트림 연결이 종료되었습니다."));
    };
  });
}

export class AudioCaptureController {
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private failed = false;
  private stopping = false;
  private recovering = false;
  private generation = 0;
  private started = false;
  private preferredDeviceId: string | undefined;
  private readonly onFatalError?: (message: string) => void;
  private readonly trackEndedHandler = () => {
    void this.recoverAudioInput();
  };
  private readonly deviceChangeHandler = () => {
    void this.recoverAudioInput();
  };
  private readonly resumeHandler = () => {
    void this.resumeAudioContext();
  };

  constructor(onFatalError?: (message: string) => void) {
    this.onFatalError = onFatalError;
  }

  private isCurrent(generation: number): boolean {
    return generation === this.generation && !this.failed && !this.stopping;
  }

  private requireCurrent(generation: number): void {
    if (!this.isCurrent(generation)) throw new Error("오디오 입력 시작이 취소되었습니다.");
  }

  private fail(message: string, generation: number): void {
    if (!this.isCurrent(generation)) return;
    this.failed = true;
    this.detachDeviceListeners();
    if (this.worklet) this.worklet.port.onmessage = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) {
      this.socket.close(AUDIO_BACKPRESSURE_CLOSE_CODE, "audio stream unavailable");
    }
    this.onFatalError?.(message);
  }

  private async openAudioStream(deviceId?: string): Promise<MediaStream> {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: deviceId ? { exact: deviceId } : undefined,
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
    } catch (error) {
      throw new Error(microphoneErrorMessage(error));
    }
  }

  private attachTrackListeners(stream: MediaStream): void {
    stream.getAudioTracks().forEach((track) => {
      track.addEventListener("ended", this.trackEndedHandler);
    });
  }

  private detachTrackListeners(stream: MediaStream | null): void {
    stream?.getAudioTracks().forEach((track) => {
      track.removeEventListener("ended", this.trackEndedHandler);
    });
  }

  private attachDeviceListeners(): void {
    navigator.mediaDevices?.addEventListener?.("devicechange", this.deviceChangeHandler);
    document.addEventListener("visibilitychange", this.resumeHandler);
    window.addEventListener("focus", this.resumeHandler);
    window.addEventListener("pageshow", this.resumeHandler);
  }

  private detachDeviceListeners(): void {
    navigator.mediaDevices?.removeEventListener?.("devicechange", this.deviceChangeHandler);
    document.removeEventListener("visibilitychange", this.resumeHandler);
    window.removeEventListener("focus", this.resumeHandler);
    window.removeEventListener("pageshow", this.resumeHandler);
    this.detachTrackListeners(this.stream);
  }

  private async resumeAudioContext(): Promise<void> {
    const generation = this.generation;
    const context = this.context;
    if (!this.isCurrent(generation) || !context || context.state !== "suspended") return;
    try {
      await context.resume();
    } catch (error) {
      this.fail(`절전/재개 후 오디오 입력을 다시 시작하지 못했습니다. ${microphoneErrorMessage(error)}`, generation);
    }
  }

  private async recoverAudioInput(): Promise<void> {
    const generation = this.generation;
    const context = this.context;
    const worklet = this.worklet;
    if (
      this.recovering ||
      this.stopping ||
      this.failed ||
      !context ||
      !worklet ||
      !navigator.mediaDevices?.enumerateDevices
    ) {
      return;
    }

    this.recovering = true;
    let replacement: MediaStream | null = null;
    try {
      const devices = (await navigator.mediaDevices.enumerateDevices()).filter(
        (device) => device.kind === "audioinput",
      );
      if (!this.isCurrent(generation)) return;
      const currentLive = this.stream?.getAudioTracks().some((track) => track.readyState === "live") ?? false;
      const preferredStillPresent =
        !this.preferredDeviceId || devices.some((device) => device.deviceId === this.preferredDeviceId);
      if (currentLive && preferredStillPresent) return;

      const recoveryDeviceId = chooseRecoveryDevice(this.preferredDeviceId, devices);
      if (!recoveryDeviceId && devices.length === 0) {
        throw new Error("사용 가능한 대체 마이크가 없습니다.");
      }

      replacement = await this.openAudioStream(recoveryDeviceId);
      if (!this.isCurrent(generation)) return;

      const replacementSource = context.createMediaStreamSource(replacement);
      replacementSource.connect(worklet);

      const previousStream = this.stream;
      this.detachTrackListeners(previousStream);
      this.source?.disconnect();
      this.stream = replacement;
      this.source = replacementSource;
      this.preferredDeviceId = recoveryDeviceId;
      this.attachTrackListeners(replacement);
      previousStream?.getTracks().forEach((track) => track.stop());
      replacement = null; // Ownership transferred to this generation's stream field.
      await this.resumeAudioContext();
    } catch (error) {
      this.fail(`마이크 연결이 끊겼고 자동 재연결에 실패했습니다. ${microphoneErrorMessage(error)}`, generation);
    } finally {
      replacement?.getTracks().forEach((track) => track.stop());
      if (generation === this.generation) this.recovering = false;
    }
  }

  async start(deviceId?: string, expectedSessionId?: string): Promise<number> {
    if (this.started && this.context && this.isCurrent(this.generation)) return this.context.sampleRate;
    const socketPath = audioSocketPath(expectedSessionId);
    const closing = this.stop();
    const generation = this.generation;
    this.preferredDeviceId = deviceId;

    try {
      await closing;
      this.requireCurrent(generation);
      const stream = await this.openAudioStream(deviceId);
      if (!this.isCurrent(generation)) {
        stream.getTracks().forEach((track) => track.stop());
        this.requireCurrent(generation);
      }
      this.stream = stream;
      const socket = new WebSocket(getWebSocketUrl(socketPath));
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      const config = await waitForAudioConfig(socket);
      this.requireCurrent(generation);

      socket.onerror = () => {
        this.fail("오디오 스트림 서버와의 연결에 오류가 발생해 세션을 중지합니다.", generation);
      };
      socket.onclose = (event) => {
        if (event.code !== 1000) {
          this.fail(event.reason || "오디오 스트림 연결이 종료되어 세션을 중지합니다.", generation);
        }
      };

      const context = new AudioContext({ sampleRate: config.sample_rate });
      this.context = context;
      await context.audioWorklet.addModule("/audio-worklet.js");
      this.requireCurrent(generation);
      await context.resume();
      this.requireCurrent(generation);

      this.source = context.createMediaStreamSource(stream);
      this.worklet = new AudioWorkletNode(context, "langtextflow-audio-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
        processorOptions: { chunkFrames: Math.round(config.sample_rate * 0.25) },
      });
      this.sink = context.createGain();
      this.sink.gain.value = 0;
      this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (!this.isCurrent(generation) || socket.readyState !== WebSocket.OPEN) return;
        if (!canQueueAudioFrame(socket.bufferedAmount, event.data.byteLength)) {
          this.fail("오디오 처리 속도가 입력을 따라가지 못해 세션을 안전하게 중지합니다.", generation);
          return;
        }
        socket.send(event.data);
      };
      this.source.connect(this.worklet);
      this.worklet.connect(this.sink);
      this.sink.connect(context.destination);
      this.attachTrackListeners(stream);
      this.attachDeviceListeners();
      this.started = true;
      return context.sampleRate;
    } catch (error) {
      if (generation === this.generation) await this.stop();
      throw error;
    }
  }

  async stop(): Promise<void> {
    this.generation++;
    this.stopping = true;
    this.detachDeviceListeners();
    const { socket, stream, context, source, worklet, sink, failed } = this;
    this.socket = null;
    this.stream = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
    this.preferredDeviceId = undefined;
    this.recovering = false;
    this.started = false;
    this.failed = false;
    if (worklet) worklet.port.onmessage = null;
    if (socket?.readyState === WebSocket.OPEN && !failed) socket.send("end");
    worklet?.disconnect();
    source?.disconnect();
    sink?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000);
    this.stopping = false;
    // All shared ownership is detached before this await; a later start owns its own resources.
    if (context && context.state !== "closed") await context.close();
  }
}
