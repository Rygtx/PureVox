export class AudioCapture {
    constructor() {
        this._stream = null;
        this._context = null;
        this._processor = null;
        this._encoderWorker = null;
        this._onOpusData = null;
        this._onLevel = null;
        this._pcmBuffer = new Float32Array(0);
        this._frameSize = 960;
        // Debug info
        this._sampleRate = 0;
        this._deviceSampleRate = 0;
        this._totalEncoded = 0;
    }

    static async listMics() {
        const devices = await navigator.mediaDevices.enumerateDevices();
        return devices.filter(d => d.kind === 'audioinput');
    }

    async start(deviceId) {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('浏览器不支持麦克风访问（需要 HTTPS）');
        }

        this._context = new AudioContext();
        if (this._context.state === 'suspended') {
            await this._context.resume();
        }
        this._sampleRate = this._context.sampleRate;

        const constraints = { audio: { channelCount: 1 } };
        if (deviceId) {
            constraints.audio.deviceId = { exact: deviceId };
        }

        this._stream = await navigator.mediaDevices.getUserMedia(constraints);
        // Get actual device sample rate from MediaTrackSettings
        try {
            const track = this._stream.getAudioTracks()[0];
            const settings = track.getSettings();
            this._deviceSampleRate = settings.sampleRate || 0;
        } catch (e) {
            this._deviceSampleRate = 0;
        }
        const source = this._context.createMediaStreamSource(this._stream);

        this._encoderWorker = new Worker('wasm/opus-encoder-worker.js');
        this._encoderWorker.onmessage = (e) => {
            if (e.data.type === 'opus' && this._onOpusData) {
                this._onOpusData(new Uint8Array(e.data.data));
            }
        };

        this._processor = this._context.createScriptProcessor(1024, 1, 1);
        this._processor.onaudioprocess = (e) => {
            const input = e.inputBuffer.getChannelData(0);

            let peak = 0;
            for (let i = 0; i < input.length; i++) {
                const abs = Math.abs(input[i]);
                if (abs > peak) peak = abs;
            }
            if (this._onLevel) this._onLevel(peak);

            const newBuf = new Float32Array(this._pcmBuffer.length + input.length);
            newBuf.set(this._pcmBuffer);
            newBuf.set(input, this._pcmBuffer.length);
            this._pcmBuffer = newBuf;

            while (this._pcmBuffer.length >= this._frameSize) {
                const frame = this._pcmBuffer.slice(0, this._frameSize);
                this._pcmBuffer = this._pcmBuffer.slice(this._frameSize);
                this._totalEncoded++;
                if (this._onOpusData) {
                    this._encoderWorker.postMessage({ type: 'encode', data: frame },
                        [frame.buffer]);
                }
            }
        };

        source.connect(this._processor);
        this._processor.connect(this._context.destination);
    }

    stop() {
        if (this._encoderWorker) {
            this._encoderWorker.postMessage({ type: 'close' });
            this._encoderWorker = null;
        }
        if (this._processor) {
            try { this._processor.disconnect(); } catch {}
            this._processor = null;
        }
        if (this._stream) {
            this._stream.getTracks().forEach(t => t.stop());
            this._stream = null;
        }
        if (this._context) {
            try { this._context.close(); } catch {}
            this._context = null;
        }
        this._pcmBuffer = new Float32Array(0);  // 清空残留，防止不全帧被发送
        this._totalEncoded = 0;
    }

    set onOpusData(fn) { this._onOpusData = fn; }
    set onLevel(fn) { this._onLevel = fn; }

    // Debug info getters
    get sampleRate() { return this._sampleRate; }
    get deviceSampleRate() { return this._deviceSampleRate; }
    get encoderFrameSize() { return this._frameSize; }
    get encoderSampleRate() { return 48000; }     // Opus encoder hardcoded
    get encoderBitrate() { return 32000; }         // Opus encoder hardcoded
    get bufferBacklog() { return this._pcmBuffer.length; }
    get totalEncoded() { return this._totalEncoded; }
    get streaming() { return this._stream !== null; }
}
