class LangTextFlowAudioProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.chunkFrames = Math.max(1024, options.processorOptions?.chunkFrames ?? 4096);
    this.buffer = new Float32Array(this.chunkFrames);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    let sourceOffset = 0;
    while (sourceOffset < channel.length) {
      const count = Math.min(this.chunkFrames - this.offset, channel.length - sourceOffset);
      this.buffer.set(channel.subarray(sourceOffset, sourceOffset + count), this.offset);
      this.offset += count;
      sourceOffset += count;

      if (this.offset === this.chunkFrames) {
        const output = this.buffer;
        this.port.postMessage(output.buffer, [output.buffer]);
        this.buffer = new Float32Array(this.chunkFrames);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("langtextflow-audio-processor", LangTextFlowAudioProcessor);
