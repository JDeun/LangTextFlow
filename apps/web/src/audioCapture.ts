import { getWebSocketUrl } from "./api";
import {
  AUDIO_BACKPRESSURE_CLOSE_CODE,
  AUDIO_PROTOCOL_ERROR_CODE,
  canQueueAudioFrame,
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
      if (event.code !== 1000) {
        window.clearTimeout(timeout);
        reject(new Error(event.reason || "오디오 스트림 연결이 종료되었습니다."));
      }
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
  private readonly onFatalError?: (message: string) => void;

  constructor(onFatalError?: (message: string) => void) {
    this.onFatalError = onFatalError;
  }

  private fail(message: string): void {
    if (this.failed || this.stopping) return;
    this.failed = true;
    if (this.worklet) this.worklet.port.onmessage = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) {
      this.socket.close(AUDIO_BACKPRESSURE_CLOSE_CODE, "audio stream unavailable");
    }
    this.onFatalError?.(message);
  }

  async start(deviceId?: string): Promise<number> {
    if (this.context) return this.context.sampleRate;
    this.failed = false;
    this.stopping = false;

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

      this.socket.onerror = () => {
        this.fail("오디오 스트림 서버와의 연결에 오류가 발생해 세션을 중지합니다.");
      };
      this.socket.onclose = (event) => {
        if (!this.stopping && event.code !== 1000) {
          this.fail(event.reason || "오디오 스트림 연결이 종료되어 세션을 중지합니다.");
        }
      };

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
        if (!canQueueAudioFrame(socket.bufferedAmount, event.data.byteLength)) {
          this.fail("오디오 처리 속도가 입력을 따라가지 못해 세션을 안전하게 중지합니다.");
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
    this.stopping = true;
    if (this.worklet) this.worklet.port.onmessage = null;
    if (this.socket?.readyState === WebSocket.OPEN && !this.failed) this.socket.send("end");
    this.worklet?.disconnect();
    this.source?.disconnect();
    this.sink?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context && this.context.state !== "closed") await this.context.close();
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) this.socket.close(1000);
    this.socket = null;
    this.stream = null;
    this.context = null;
    this.source = null;
    this.worklet = null;
    this.sink = null;
    this.failed = false;
    this.stopping = false;
  }
}
