class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const config = options.processorOptions || {};
    this.targetRate = config.targetRate || 24000;
    this.chunkFrames = Math.max(1, Math.round(this.targetRate * (config.chunkMs || 100) / 1000));
    this.ratio = sampleRate / this.targetRate;
    this.pending = [];
    this.position = 0;
    this.port.onmessage = ({ data }) => {
      if (data?.type !== "flush") return;
      this.emitPending(true);
      this.port.postMessage({ type: "flushed" });
    };
  }

  emitPending(flush = false) {
    while (this.pending.length >= this.chunkFrames || (flush && this.pending.length > 0)) {
      const frameCount = flush ? Math.min(this.chunkFrames, this.pending.length) : this.chunkFrames;
      const floats = this.pending.splice(0, frameCount);
      const pcm = new Int16Array(floats.length);
      for (let index = 0; index < floats.length; index += 1) {
        const value = floats[index];
        pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || input.length === 0) return true;

    while (this.position < input.length) {
      const left = Math.floor(this.position);
      const right = Math.min(left + 1, input.length - 1);
      const fraction = this.position - left;
      const sample = input[left] + (input[right] - input[left]) * fraction;
      this.pending.push(Math.max(-1, Math.min(1, sample)));
      this.position += this.ratio;
    }
    this.position -= input.length;

    this.emitPending();
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
