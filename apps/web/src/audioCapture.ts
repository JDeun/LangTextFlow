import { getWebSocketUrl } from "./api";
import { parseAudioSocketConfig, shouldSendAudioFrame } from "./audioSocketPolicy";

export interface AudioInputDevice {
  deviceId: string;
  label: string;
}

export async function requestAudioInputs(): Promise<AudioInputDevice[]> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("이 환경에서는 마이크 입력을 사용할 수 없습니다.");
  }
  const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  permissionStream.getTracks().forEach((track) => track.stop());
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === "audioinput")
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `오디오 입력 ${index + 1}`,
    }));
}

function waitForAudioConfig(socket: WebSocket) {
  return new Promise<ReturnType<typeof parseAudioSocketConfig> extends infer T ? Exclude<T, null> : never>(
    (resolve, reject) => {
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error("오디오 서버 응답이 없습니다."));
      }, 5000);

      const fail = (error: Error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        reject(error);
      };

      socket.onmessage = (event) => {
        if (settled) return;
        const payload = parseAudioSocketConfig(event.data);
        if (!payload) {
          socket.close(1003, "invalid audio config");
          fail(new Error("오디오 서버 설정 응답이 올바르지 않습니다."));
          return;
        }
        settled = true;
        window.clearTimeout(timeout);
        resolve(payload);
      };
      socket.onerror = () => {
        fail(new Error("오디오 스트림 서버에 연결할 수 없습니다."));
      };
      socket.onclose = (event) => {
        if (event.code !== 1000) {
          fail(new Error(event.reason || "오디오 스트림 연결이 종료되었습니다."));
        }
      };
    },
  );
}

export class AudioCaptureController {
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private droppedFrames = 0;

  get droppedFrameCount(): number {
    return this.droppedFrames;
  }

  async start(deviceId?: string): Promise<number> {
    if (this.context) return this.context.sampleRate;

    this.droppedFrames = 0;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    });

    try {
      this.socket = new WebSocket(getWebSocketUrl("/ws/audio"));
      this.socket.binaryType = "arraybuffer";
      const config = await waitForAudioConfig(this.socket);

      this.context = new AudioContext({ sampleRate: config.sample_rate });
      await this.context.audioWorklet.addModule("/audio-worklet.js");
      await this.context.resume();

      this.source = this.context.createMediaStreamSource(this.stream);
      this.worklet = new AudioWorkletNode(this.context, "langtextflow-audio-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
        processorOptions: { chunkFrames: Math.round(config.sample_rate * 0.25) },
      });
      this.sink = this.context.createGain();
      this.sink.gain.value = 0;
      this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        const socket = this.socket;
        if (socket?.readyState !== WebSocket.OPEN) return;
        if (!shouldSendAudioFrame(socket.bufferedAmount)) {
          this.droppedFrames += 1;
          return;
        }
        socket.send(event.data);
      };
      this.source.connect(this.worklet);
      this.worklet.connect(this.sink);
      this.sink.connect(this.context.destination);
      return this.context.sampleRate;
    } catch (error) {
      await this.stop();
      throw error;
    }
  }

  async stop(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send("end");
    this.worklet?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context) await this.context.close();
    this.socket?.close(1000);
    this.socket = null;
    this.stream = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
  }
}
