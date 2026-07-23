// AudioWorkletProcessor: downsamples the mic's native float32 audio (whatever
// rate the AudioContext runs at, usually 48kHz) to mono PCM16 @ 16kHz using
// ratio-based linear-interpolation resampling (does NOT assume 48kHz -- uses
// the `sampleRate` global the worklet runtime provides). Emits ~20ms chunks
// (320 samples @ 16kHz = 640 bytes) as transferable ArrayBuffers.
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 320; // 20ms @ 16kHz

class PCMDownsamplerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / TARGET_RATE; // `sampleRate` = worklet global (context rate)
    this._carry = new Float32Array(0); // leftover input samples from the previous call
    this._pos = 0; // fractional read position within _carry+incoming
    this._pending = []; // accumulated Int16 samples not yet flushed as a chunk
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length || !input[0] || !input[0].length) return true;
    const chan = input[0];

    const merged = new Float32Array(this._carry.length + chan.length);
    merged.set(this._carry, 0);
    merged.set(chan, this._carry.length);

    let pos = this._pos;
    while (true) {
      const idx = Math.floor(pos);
      if (idx + 1 >= merged.length) break;
      const frac = pos - idx;
      const sample = merged[idx] + (merged[idx + 1] - merged[idx]) * frac;
      const clamped = Math.max(-1, Math.min(1, sample)) * 0x7fff;
      this._pending.push(clamped);
      if (this._pending.length >= CHUNK_SAMPLES) {
        const int16 = new Int16Array(this._pending.splice(0, CHUNK_SAMPLES));
        this.port.postMessage(int16.buffer, [int16.buffer]);
      }
      pos += this._ratio;
    }

    // Keep the unread tail (and fractional offset) for the next 128-frame call.
    const carryStart = Math.floor(pos);
    this._carry = merged.slice(carryStart);
    this._pos = pos - carryStart;

    return true;
  }
}

registerProcessor("pcm-downsampler", PCMDownsamplerProcessor);
