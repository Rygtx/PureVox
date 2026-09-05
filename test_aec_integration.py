"""AEC 集成诊断——排查实际运行中 AEC 为什么达不到离线效果。

检查项：
1. 模型输入输出对齐（滞后 1 hop 是否被正确处理）
2. FarTap 时钟对齐（远端/近端设备时钟差）
3. Far gain 影响
4. 缓存热身行为
5. 远端信号延迟补偿

用法：python test_aec_integration.py
"""

import os
import sys
import time
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import wave

SAMPLE_RATE = 48000
HOP_LENGTH = 480
CACHE_DIM = 215504

MODEL_PATH = os.path.join(_HERE, "models", "purevox_aec_202609_cpx_ep0316.onnx")
TEST_DIR = os.path.join(_HERE, "aec")


def read_wav_mono_f32(path):
    with wave.open(path, "rb") as wf:
        sr, nch, sw, nf = wf.getframerate(), wf.getnchannels(), wf.getsampwidth(), wf.getnframes()
        raw = wf.readframes(nf)
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width {sw}")
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    return data, sr


def rms_db(x):
    return float(10 * np.log10(max(np.mean(x ** 2), 1e-20)))


def safe_rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def segment_erle_db(mic_seg, out_seg):
    echo_power = np.mean(mic_seg ** 2)
    residual = np.mean((mic_seg - out_seg) ** 2)
    return 10 * np.log10(max(echo_power, 1e-20) / max(residual, 1e-20))


def load_model(path):
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(path, sess_options=so,
                                providers=["CPUExecutionProvider"])
    return sess, [i.name for i in sess.get_inputs()], [o.name for o in sess.get_outputs()]


def test_direct_passthrough(far, mic, near):
    """测试1：直接喂入（无 FarTap 对齐），验证模型本身效果。"""
    print("=" * 70)
    print("TEST 1: Direct passthrough (no FarTap alignment)")
    print("=" * 70)

    sess, in_names, out_names = load_model(MODEL_PATH)
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

    out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH
        mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
        far_hop = far[s:e].reshape(1, -1).astype(np.float32)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        out_buf[s:e] = enh

    # 模型输出滞后 1 hop，对齐
    aligned = np.zeros_like(out_buf)
    aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]

    erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
    print(f"  ERLE (aligned): {erle:+.1f} dB")
    return erle


def test_delayed_far(far, mic, near, delay_ms):
    """测试2：模拟远端延迟（实际场景：扬声器播放→麦克风拾取有延迟）。"""
    print(f"\n{'='*70}")
    print(f"TEST 2: Far signal delayed by {delay_ms}ms")
    print("=" * 70)

    sess, in_names, out_names = load_model(MODEL_PATH)
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    delay_samples = int(delay_ms * SAMPLE_RATE / 1000)
    n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

    # 延迟 far 信号
    far_delayed = np.zeros(len(far) + delay_samples, dtype=np.float32)
    far_delayed[delay_samples:delay_samples+len(far)] = far

    out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH
        mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
        far_hop = far_delayed[s:e].reshape(1, -1).astype(np.float32)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        out_buf[s:e] = enh

    aligned = np.zeros_like(out_buf)
    aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]

    erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
    print(f"  ERLE (aligned): {erle:+.1f} dB")
    return erle


def test_fartap_alignment(far, mic, near):
    """测试3：模拟 FarTap 时钟对齐行为（微调消费速率）。"""
    print(f"\n{'='*70}")
    print("TEST 3: FarTap clock alignment simulation")
    print("=" * 70)

    from pvengine.dsp.far_sync import FarTap

    sess, in_names, out_names = load_model(MODEL_PATH)
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

    tap = FarTap(48000, HOP_LENGTH)
    out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)

    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH

        # 模拟实际场景：far 设备可能有微小时钟漂移
        # 这里用理想 48k，但 FarTap 内部有 PI 伺服
        tap.push(far[s:e].tolist())

        mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
        far_ref = np.asarray(tap.pull(), dtype=np.float32).reshape(1, -1)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_ref, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        out_buf[s:e] = enh

        if h % 100 == 0:
            d = tap.diag()
            print(f"  hop {h:4d}: level={d['level']:4d} rate={d['rate']:.4f} "
                  f"conceals={d['conceals']} drops={d['drops']}")

    aligned = np.zeros_like(out_buf)
    aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]

    erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
    print(f"  ERLE: {erle:+.1f} dB")
    return erle


def test_warmup(far, mic, near):
    """测试4：缓存热身行为——前 N hop 的效果变化。"""
    print(f"\n{'='*70}")
    print("TEST 4: Cache warmup behavior")
    print("=" * 70)

    sess, in_names, out_names = load_model(MODEL_PATH)
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

    out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
    cache_norms = []
    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH
        mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
        far_hop = far[s:e].reshape(1, -1).astype(np.float32)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        out_buf[s:e] = enh
        cache_norms.append(float(np.sqrt(np.sum(cache**2))))

    # 逐段 ERLE（每 100ms）
    seg_len = HOP_LENGTH * 10  # 100ms
    print(f"\n  ERLE progression (100ms segments):")
    print(f"  {'seg':>4s}  {'t(ms)':>6s}  {'erle':>7s}  {'cache_norm':>10s}")
    for i in range(min(50, n_hops * HOP_LENGTH // seg_len)):
        s = i * seg_len
        e = s + seg_len
        erle = segment_erle_db(mic[s:e], out_buf[s:e])
        cn = cache_norms[min(i*10, len(cache_norms)-1)]
        print(f"  {i:4d}  {i*100:6d}  {erle:+7.1f}  {cn:10.1f}")

    return cache_norms


def test_far_gain_impact(far, mic, near):
    """测试5：Far gain 对效果的影响。"""
    print(f"\n{'='*70}")
    print("TEST 5: Far gain impact")
    print("=" * 70)

    for gain_db in [-20, -10, -6, 0, 6, 12]:
        sess, in_names, out_names = load_model(MODEL_PATH)
        cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
        gain = 10.0 ** (gain_db / 20.0)
        n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

        out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
        for h in range(n_hops):
            s = h * HOP_LENGTH
            e = s + HOP_LENGTH
            mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
            far_hop = (far[s:e] * gain).reshape(1, -1).astype(np.float32)

            outs = sess.run(out_names, {
                "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
            })
            od = dict(zip(out_names, outs))
            enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
            cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
            out_buf[s:e] = enh

        aligned = np.zeros_like(out_buf)
        aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]
        erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
        print(f"  far_gain={gain_db:+3d}dB: ERLE={erle:+.1f} dB")


def test_misaligned_signals(far, mic, near):
    """测试6：信号未对齐（实际场景：远端和近端不同步）。"""
    print(f"\n{'='*70}")
    print("TEST 6: Signal misalignment (simulating real-world timing)")
    print("=" * 70)

    for shift_ms in [0, 10, 20, 50, 100, 200, 300]:
        shift_samples = int(shift_ms * SAMPLE_RATE / 1000)
        sess, in_names, out_names = load_model(MODEL_PATH)
        cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
        n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

        out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
        for h in range(n_hops):
            s = h * HOP_LENGTH
            e = s + HOP_LENGTH

            mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
            # far 信号偏移
            fs = s + shift_samples
            fe = e + shift_samples
            far_hop = np.zeros((1, HOP_LENGTH), dtype=np.float32)
            if fs < len(far):
                avail = min(HOP_LENGTH, len(far) - fs)
                far_hop[0, :avail] = far[fs:fs+avail]

            outs = sess.run(out_names, {
                "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
            })
            od = dict(zip(out_names, outs))
            enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
            cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
            out_buf[s:e] = enh

        aligned = np.zeros_like(out_buf)
        aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]
        erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
        print(f"  shift={shift_ms:4d}ms: ERLE={erle:+.1f} dB")


def test_realistic_pipeline(far, mic, near):
    """测试7：模拟完整 pipeline（FarTap + AEC + 对齐）。"""
    print(f"\n{'='*70}")
    print("TEST 7: Realistic pipeline simulation")
    print("=" * 70)

    from pvengine.dsp.far_sync import FarTap

    sess, in_names, out_names = load_model(MODEL_PATH)
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    n_hops = min(len(far), len(mic), len(near)) // HOP_LENGTH

    # 模拟实际场景：far 设备有微小时钟漂移（±2%）
    # 用正弦波模拟时钟漂移
    t = np.arange(len(far)) / SAMPLE_RATE
    drift = 1.0 + 0.02 * np.sin(2 * np.pi * 0.01 * t)  # ±2% @ 0.01Hz
    far_drifted = np.interp(
        np.cumsum(drift) * SAMPLE_RATE,
        np.arange(len(far)),
        far
    ).astype(np.float32)

    tap = FarTap(48000, HOP_LENGTH)
    out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)

    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH

        # 推入 far（可能有多余样本，FarTap 会按需消费）
        tap.push(far_drifted[s:e].tolist())

        mic_hop = mic[s:e].reshape(1, -1).astype(np.float32)
        far_ref = np.asarray(tap.pull(), dtype=np.float32).reshape(1, -1)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_ref, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        out_buf[s:e] = enh

    aligned = np.zeros_like(out_buf)
    aligned[:-HOP_LENGTH] = out_buf[HOP_LENGTH:]

    erle = segment_erle_db(mic[:n_hops*HOP_LENGTH], aligned)
    d = tap.diag()
    print(f"  ERLE: {erle:+.1f} dB")
    print(f"  FarTap stats: level={d['level']} rate={d['rate']:.4f} "
          f"conceals={d['conceals']} drops={d['drops']}")
    return erle


def main():
    print("AEC Integration Diagnostic")
    print("=" * 70)

    # 读取测试数据
    print("\nLoading test data...")
    far, sr = read_wav_mono_f32(os.path.join(TEST_DIR, "test_000019_far.wav"))
    mic, _ = read_wav_mono_f32(os.path.join(TEST_DIR, "test_000019_mic.wav"))
    near, _ = read_wav_mono_f32(os.path.join(TEST_DIR, "test_000019_near.wav"))
    print(f"  far: {len(far)} samples, RMS={safe_rms(far):.4f} ({rms_db(far):.1f} dBFS)")
    print(f"  mic: {len(mic)} samples, RMS={safe_rms(mic):.4f} ({rms_db(mic):.1f} dBFS)")
    print(f"  near: {len(near)} samples, RMS={safe_rms(near):.4f} ({rms_db(near):.1f} dBFS)")

    # 运行所有测试
    results = {}
    results["direct"] = test_direct_passthrough(far, mic, near)
    results["delay_100ms"] = test_delayed_far(far, mic, near, 100)
    results["delay_200ms"] = test_delayed_far(far, mic, near, 200)
    results["delay_300ms"] = test_delayed_far(far, mic, near, 300)
    results["fartap"] = test_fartap_alignment(far, mic, near)
    results["realistic"] = test_realistic_pipeline(far, mic, near)
    test_warmup(far, mic, near)
    test_far_gain_impact(far, mic, near)
    test_misaligned_signals(far, mic, near)

    # 汇总
    print(f"\n{'='*70}")
    print("SUMMARY")
    print("=" * 70)
    print(f"  Direct passthrough:     {results['direct']:+.1f} dB (baseline)")
    print(f"  With FarTap alignment:  {results['fartap']:+.1f} dB")
    print(f"  Realistic pipeline:     {results['realistic']:+.1f} dB")
    print(f"  Far delayed 100ms:      {results['delay_100ms']:+.1f} dB")
    print(f"  Far delayed 200ms:      {results['delay_200ms']:+.1f} dB")
    print(f"  Far delayed 300ms:      {results['delay_300ms']:+.1f} dB")

    print(f"\n  Key insight:")
    if results["realistic"] < results["direct"] - 3:
        print(f"    FarTap alignment causes significant degradation!")
        print(f"    The PI servo or prime period may need tuning.")
    elif results["delay_200ms"] < results["direct"] - 5:
        print(f"    Far signal delay >200ms hurts AEC significantly.")
        print(f"    Real-world echo delay may exceed model's training range.")
    else:
        print(f"    Pipeline simulation shows similar results to direct.")
        print(f"    The issue may be in configuration, not the pipeline.")


if __name__ == "__main__":
    main()
