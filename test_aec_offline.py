"""离线 AEC 效果测试 -- 用 test_000019 三件套 WAV 验证模型回声消除能力。

模型契约：输入 mic/far 各 480 样本，输出 enh_hop 滞后 1 hop。
即 enh[t] 是对 mic[t-1] 的增强结果（第一帧 enh[0] 无意义）。

用法：python test_aec_offline.py [--far-gain-db GAIN] [--save-wav] [--model PATH]
"""

import argparse
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

FILES = {
    "far":  os.path.join(TEST_DIR, "test_000019_far.wav"),
    "mic":  os.path.join(TEST_DIR, "test_000019_mic.wav"),
    "near": os.path.join(TEST_DIR, "test_000019_near.wav"),
}


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
    """ERLE = 10*log10( E[mic^2] / E[(mic-out)^2] )
    正值 = 回声被衰减，负值 = 恶化。"""
    echo_power = np.mean(mic_seg ** 2)
    residual = np.mean((mic_seg - out_seg) ** 2)
    return 10 * np.log10(max(echo_power, 1e-20) / max(residual, 1e-20))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--far-gain-db", type=float, default=0.0)
    parser.add_argument("--save-wav", action="store_true")
    parser.add_argument("--model", default=MODEL_PATH)
    parser.add_argument("--skip-align", action="store_true",
                        help="跳过输出对齐修正（默认启用 1-hop 延迟修正）")
    args = parser.parse_args()

    # ── 加载模型 ──
    print(f"[1] Loading model: {args.model}")
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(args.model, sess_options=so,
                                providers=["CPUExecutionProvider"])
    in_names = [i.name for i in sess.get_inputs()]
    out_names = [o.name for o in sess.get_outputs()]
    print(f"   Inputs:  {in_names}")
    print(f"   Outputs: {out_names}")
    for inp in sess.get_inputs():
        print(f"     {inp.name}: shape={inp.shape}, dtype={inp.type}")

    # ── 读 WAV ──
    print(f"\n[2] Read test WAVs:")
    wavs = {}
    for key, path in FILES.items():
        if not os.path.isfile(path):
            print(f"   MISSING: {path}")
            sys.exit(1)
        data, sr = read_wav_mono_f32(path)
        assert sr == SAMPLE_RATE, f"{key} sr={sr} != {SAMPLE_RATE}"
        wavs[key] = data
        print(f"   {key}: {len(data)} samples ({len(data)/SAMPLE_RATE:.3f}s) "
              f"RMS={safe_rms(data):.6f} ({rms_db(data):.1f} dBFS)")

    min_len = min(len(wavs["far"]), len(wavs["mic"]), len(wavs["near"]))
    n_hops = min_len // HOP_LENGTH
    print(f"   Total: {n_hops} hops ({n_hops*10} ms)")

    far_gain = 10.0 ** (args.far_gain_db / 20.0)

    # ── 逐 hop 推理 ──
    print(f"\n[3] Inference (far_gain={args.far_gain_db}dB)...")
    cache = np.zeros((1, CACHE_DIM), dtype=np.float32)
    # enh_buf: 存放每 hop 的原始输出（未对齐）
    enh_raw = np.zeros((n_hops, HOP_LENGTH), dtype=np.float32)

    t0 = time.perf_counter()
    for h in range(n_hops):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH
        mic_hop = wavs["mic"][s:e].reshape(1, -1).astype(np.float32)
        far_hop = (wavs["far"][s:e] * far_gain).reshape(1, -1).astype(np.float32)

        outs = sess.run(out_names, {
            "mic_hop": mic_hop, "far_hop": far_hop, "cache_in": cache,
        })
        od = dict(zip(out_names, outs))
        enh_raw[h] = np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)
        cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)

    elapsed = time.perf_counter() - t0
    rtf = elapsed / (n_hops * HOP_LENGTH / SAMPLE_RATE)
    print(f"   Time: {elapsed:.3f}s, RTF={rtf:.4f}x ({1.0/rtf:.1f}x realtime)")

    # ── 输出对齐：模型 enh[t] 对应 mic[t-1]（滞后 1 hop） ──
    # enh_raw[0] 无意义（无 t=-1），enh_raw[t] 对应 mic[t-1]
    # 所以对齐后 out_aligned[h] = enh_raw[h+1]，对应 mic[h]
    if not args.skip_align:
        out_buf = np.zeros(n_hops * HOP_LENGTH, dtype=np.float32)
        for h in range(n_hops - 1):
            s = h * HOP_LENGTH
            e = s + HOP_LENGTH
            out_buf[s:e] = enh_raw[h + 1]  # enh[t+1] -> mic[t]
        label = "aligned (1-hop lag compensated)"
    else:
        out_buf = enh_raw.reshape(-1)[:n_hops * HOP_LENGTH]
        label = "raw (no alignment)"

    near = wavs["near"][:n_hops * HOP_LENGTH]
    mic_full = wavs["mic"][:n_hops * HOP_LENGTH]
    far_full = wavs["far"][:n_hops * HOP_LENGTH]

    # ── 信号关系验证 ──
    print(f"\n{'='*70}")
    print(f"[4] Signal relationship analysis")
    print(f"{'='*70}")

    print(f"\n  Global signal stats:")
    print(f"    mic  (input):  {rms_db(mic_full):.1f} dBFS ({safe_rms(mic_full):.5f} RMS)")
    print(f"    far  (ref):    {rms_db(far_full):.1f} dBFS ({safe_rms(far_full):.5f} RMS)")
    print(f"    near (target): {rms_db(near):.1f} dBFS ({safe_rms(near):.5f} RMS)")
    print(f"    out  (AEC):    {rms_db(out_buf):.1f} dBFS ({safe_rms(out_buf):.5f} RMS)")
    print(f"    [{label}]")

    # mic 应该是 near + echo(far) 的叠加
    # 检查 mic - near = echo 残留
    echo_component = mic_full - near
    print(f"\n  mic - near (echo component): {rms_db(echo_component):.1f} dBFS")
    print(f"  mic/near ratio: {safe_rms(mic_full)/max(safe_rms(near),1e-10):.3f} "
          f"(>1 means echo present)")

    # 全局相关性
    corr_mic_far = np.corrcoef(mic_full, far_full)[0, 1]
    corr_out_far = np.corrcoef(out_buf, far_full)[0, 1]
    corr_out_near = np.corrcoef(out_buf, near)[0, 1]
    corr_mic_near = np.corrcoef(mic_full, near)[0, 1]
    print(f"\n  Correlations:")
    print(f"    mic <-> far:  {corr_mic_far:+.4f}")
    print(f"    mic <-> near: {corr_mic_near:+.4f}")
    print(f"    out  <-> far: {corr_out_far:+.4f} (should decrease after AEC)")
    print(f"    out  <-> near:{corr_out_near:+.4f} (should stay high)")

    # 寻找 echo delay（互相关峰值位置）
    # 用 far 和 (mic-near) 的互相关找到 echo 延迟
    echo_sig = mic_full - near
    if safe_rms(echo_sig) > 1e-6:
        max_lag = SAMPLE_RATE  # 搜索 1s 以内
        corr = np.correlate(echo_sig, far_full, mode='full')
        lags = np.arange(-len(far_full)+1, len(far_full))
        # 限制搜索范围
        mask = np.abs(lags) <= max_lag
        corr_masked = corr[mask]
        lags_masked = lags[mask]
        peak_idx = np.argmax(np.abs(corr_masked))
        peak_lag = lags_masked[peak_idx]
        peak_val = corr_masked[peak_idx] / (safe_rms(echo_sig) * safe_rms(far_full) * len(far_full))
        print(f"\n  Echo delay estimate: {peak_lag} samples ({peak_lag/SAMPLE_RATE*1000:.1f} ms)")
        print(f"  Normalized cross-correlation at echo delay: {peak_val:.4f}")

    # ── ERLE 评估 ──
    print(f"\n{'='*70}")
    print(f"[5] ERLE (Echo Return Loss Enhancement)")
    print(f"{'='*70}")

    seg_len = 4800  # 100ms
    n_segs = n_hops * HOP_LENGTH // seg_len
    erle_vals = []
    for i in range(n_segs):
        s = i * seg_len
        e = s + seg_len
        erle_vals.append(segment_erle_db(mic_full[s:e], out_buf[s:e]))

    erle_arr = np.array(erle_vals)
    valid = erle_arr[np.isfinite(erle_arr)]
    print(f"  Per 100ms segment ERLE ({len(valid)} segments):")
    print(f"    Mean:   {np.mean(valid):+.1f} dB")
    print(f"    Median: {np.median(valid):+.1f} dB")
    print(f"    Max:    {np.max(valid):+.1f} dB")
    print(f"    Min:    {np.min(valid):+.1f} dB")
    print(f"    >0dB:   {(valid>0).sum()}/{len(valid)} ({100*(valid>0).sum()/len(valid):.0f}%)")
    print(f"    >3dB:   {(valid>3).sum()}/{len(valid)} ({100*(valid>3).sum()/len(valid):.0f}%)")
    print(f"\n  ERLE distribution:")
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        print(f"    P{pct:2d}: {np.percentile(valid, pct):+.1f} dB")

    # ── 近端保真度 ──
    print(f"\n{'='*70}")
    print(f"[6] Near-end fidelity (how close out is to near)")
    print(f"{'='*70}")

    # 如果 AEC 完美，out = near
    near_err = out_buf - near
    print(f"  out - near (error):  {rms_db(near_err):.1f} dBFS")
    print(f"  near target:         {rms_db(near):.1f} dBFS")
    snr = rms_db(near) - rms_db(near_err)
    print(f"  SNR (near/error):    {snr:+.1f} dB")

    # 分段 SNR
    snr_segs = []
    for i in range(n_segs):
        s = i * seg_len
        e = s + seg_len
        ne = rms_db(near[s:e]) - rms_db(near_err[s:e])
        snr_segs.append(ne)
    snr_arr = np.array(snr_segs)
    valid_snr = snr_arr[np.isfinite(snr_arr)]
    print(f"  Per-segment SNR: mean={np.mean(valid_snr):+.1f} dB, "
          f"median={np.median(valid_snr):+.1f} dB")

    # ── Echo suppression ratio ──
    print(f"\n{'='*70}")
    print(f"[7] Echo suppression")
    print(f"{'='*70}")

    echo_in = rms_db(mic_full - near)  # mic 中的 echo 量
    echo_out = rms_db(out_buf - near)  # out 中的 echo 残留
    print(f"  Echo in mic:  {echo_in:.1f} dBFS")
    print(f"  Echo in out:  {echo_out:.1f} dBFS")
    if echo_in > -60 and echo_out > -60:
        suppression = echo_in - echo_out
        print(f"  Suppression:  {suppression:+.1f} dB ({'good' if suppression > 3 else 'weak' if suppression > 0 else 'counterproductive'})")

    # ── 逐 hop 细节（前 1 秒） ──
    print(f"\n{'='*70}")
    print(f"[8] Per-hop detail (first 1s)")
    print(f"{'='*70}")
    print(f"  {'hop':>4s} {'t(ms)':>5s}  {'mic':>7s}  {'out':>7s}  {'near':>7s}  {'far':>7s}  {'erle':>7s}  {'snr':>7s}")
    for h in range(min(100, n_hops)):
        s = h * HOP_LENGTH
        e = s + HOP_LENGTH
        m = safe_rms(mic_full[s:e])
        o = safe_rms(out_buf[s:e])
        n_ = safe_rms(near[s:e])
        f = safe_rms(far_full[s:e])
        erle = segment_erle_db(mic_full[s:e], out_buf[s:e])
        snr_h = rms_db(near[s:e]) - rms_db(out_buf[s:e] - near[s:e])
        print(f"  {h:4d} {h*10:5d}  {m:7.4f}  {o:7.4f}  {n_:7.4f}  {f:7.4f}  {erle:+7.1f}  {snr_h:+7.1f}")

    # ── 保存 WAV ──
    if args.save_wav:
        out_path = os.path.join(TEST_DIR, "test_000019_aec_out.wav")
        pcm = np.clip(out_buf, -1, 1)
        pcm_int16 = (pcm * 32767).astype(np.int16)
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm_int16.tobytes())
        print(f"\n  Output saved: {out_path}")

    # ── 结论 ──
    print(f"\n{'='*70}")
    print(f"[VERDICT]")
    print(f"{'='*70}")
    avg_erle = float(np.mean(valid)) if len(valid) > 0 else -999
    avg_snr = float(np.mean(valid_snr)) if len(valid_snr) > 0 else -999
    print(f"  Average ERLE:  {avg_erle:+.1f} dB")
    print(f"  Average SNR:   {avg_snr:+.1f} dB")
    if avg_erle > 10:
        print(f"  Result: GOOD -- AEC effectively suppresses echo")
    elif avg_erle > 3:
        print(f"  Result: PARTIAL -- AEC has some effect but weak")
    elif avg_erle > 0:
        print(f"  Result: WEAK -- AEC barely works")
    else:
        print(f"  Result: FAIL -- AEC is not working or making things worse")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
