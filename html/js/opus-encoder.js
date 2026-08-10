class PcmEncoderProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._buffer = new Float32Array(0);
        this._frameSize = 960;
        this._frameCount = 0;
    }

    process(inputs) {
        const input = inputs[0][0];
        if (!input) return true;

        let sum = 0;
        for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
        const rms = Math.sqrt(sum / input.length);

        this.port.postMessage({ type: 'level', level: rms });

        const newBuf = new Float32Array(this._buffer.length + input.length);
        newBuf.set(this._buffer);
        newBuf.set(input, this._buffer.length);
        this._buffer = newBuf;

        while (this._buffer.length >= this._frameSize) {
            const frame = this._buffer.slice(0, this._frameSize);
            this._buffer = this._buffer.slice(this._frameSize);

            this._frameCount++;
            this.port.postMessage({ type: 'pcm', data: frame.buffer, n: this._frameCount }, [frame.buffer]);
        }

        return true;
    }
}

registerProcessor('pcm-encoder-processor', PcmEncoderProcessor);
