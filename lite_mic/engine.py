# PureVox Lite Denoise Only — 纯 Python 推理引擎
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 直接用 onnxruntime + numpy 实现（与主程序 pvengine 同规格、独立精简副本）
# 规格: 48kHz, NFFT 2048, HOP 1024, Freq 1025, sqrt-Hann 窗, OLA 归一化
# 模型: v9_fft2048_band256_epoch_261.onnx (输入 spec [1,1025,1,2] + 4 状态, 输出 enhanced_spec + 4 状态)

import os
import numpy as np
import onnxruntime as ort

NFFT = 2048
HOP = 1024
FREQ = NFFT // 2 + 1  # 1025
SPEC_SIZE = FREQ * 2  # 2050 interleaved

def _sqrt_hann(n):
    w = 0.5 * (1 - np.cos(2 * np.pi * np.arange(n) / n))
    return np.sqrt(w).astype(np.float32)

class LiteDenoiseEngine:
    def __init__(self, model_path):
        if not os.path.isfile(model_path):
            raise FileNotFoundError(model_path)
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        # 保持默认 BFC arena：实测关闭仅省 ~6MB Commit，却让每帧推理 +33% CPU
        # （分配器逐帧搬运），得不偿失
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]
        # 状态尺寸从模型输入形状推断
        self.enc_c = np.zeros(self._dim("enc_c"), dtype=np.float32)
        self.dec_c = np.zeros(self._dim("dec_c"), dtype=np.float32)
        self.tfa_c = np.zeros(self._dim("tfa_c"), dtype=np.float32)
        self.inter_c = np.zeros(self._dim("inter_c"), dtype=np.float32)
        self.window = _sqrt_hann(NFFT)
        self.input_history = np.zeros(NFFT - HOP, dtype=np.float32)
        self.ola_acc = np.zeros(NFFT, dtype=np.float32)
        self.win_sum = np.zeros(NFFT, dtype=np.float32)
        # warmup 3 silent
        for _ in range(3):
            self.process(np.zeros(HOP, dtype=np.float32))

    def _dim(self, name):
        for inp in self.sess.get_inputs():
            if inp.name == name:
                shape = inp.shape
                # shape [1, N]
                if len(shape) == 2 and isinstance(shape[1], int):
                    return shape[1]
                # fallback
                return 77106 if name == "enc_c" else 53862 if name == "dec_c" else 1056 if name == "tfa_c" else 1024
        # not found -> 0
        return 0

    def _spec_from_time(self, chunk1024):
        # chunk1024: np float32 [1024]
        fft_in = np.concatenate([self.input_history, chunk1024])  # 2048
        # shift history
        self.input_history = np.concatenate([self.input_history[HOP:], chunk1024])
        fft_in = fft_in * self.window
        spec = np.fft.rfft(fft_in, n=NFFT)  # 1025 complex
        # interleaved [r0,i0,r1,i1...]
        # r0,i0 ; rNyq,0 ; r1,i1...
        # Build as [r0,0, r1,i1, ..., r1023,i1023, r1024,0] -> 2050
        out = np.zeros(SPEC_SIZE, dtype=np.float32)
        out[0] = spec[0].real
        out[1] = 0.0
        out[SPEC_SIZE - 2] = spec[FREQ - 1].real
        out[SPEC_SIZE - 1] = 0.0
        for k in range(1, FREQ - 1):
            out[k * 2] = spec[k].real
            out[k * 2 + 1] = spec[k].imag
        return out

    def _time_from_spec(self, enhanced):
        # enhanced: 2050 interleaved -> complex 1025
        spec = np.zeros(FREQ, dtype=np.complex64)
        spec[0] = complex(enhanced[0], 0)
        spec[FREQ - 1] = complex(enhanced[SPEC_SIZE - 2], 0)
        for k in range(1, FREQ - 1):
            spec[k] = complex(enhanced[k * 2], enhanced[k * 2 + 1])
        time = np.fft.irfft(spec, n=NFFT).astype(np.float32)
        # NOTE: numpy irfft already scales by 1/N, pffft backward needs extra scale.
        # pffft forward no scale, backward scale 1/N then *window. We mimic that:
        # numpy's rfft/irfft pair is normalized s.t. irfft(rfft(x)) == x
        # pffft needs windowed OLA, so we apply window and OLA same as C
        # C does: ifft_out *= scale(1/N) * window; ola += ifft; win_sum += window*window
        # For numpy, irfft already = IFFT/N * N? Actually numpy irfft = N * (mathematical IFFT).
        # To match C's windowed OLA, we just apply window after irfft (without extra 1/N)
        # Empirically keep same as C: time *= window
        time = time * self.window
        self.ola_acc += time
        self.win_sum += self.window * self.window
        out = np.zeros(HOP, dtype=np.float32)
        for i in range(HOP):
            norm = self.win_sum[i]
            out[i] = self.ola_acc[i] / norm if norm > 1e-6 else self.ola_acc[i]
        # shift
        self.ola_acc[:-HOP] = self.ola_acc[HOP:]
        self.ola_acc[-HOP:] = 0
        self.win_sum[:-HOP] = self.win_sum[HOP:]
        self.win_sum[-HOP:] = 0
        return out

    def process(self, chunk1024):
        # chunk1024: np array 1024 float32
        if chunk1024.shape[0] != HOP:
            raise ValueError("chunk must be 1024")
        spec = self._spec_from_time(chunk1024)
        # ORT inputs
        feed = {}
        # spec shape [1,1025,1,2]
        feed["spec"] = spec.reshape(1, FREQ, 1, 2).astype(np.float32)
        if "enc_c" in self.in_names:
            feed["enc_c"] = self.enc_c.reshape(1, -1).astype(np.float32) if self.enc_c.size else np.zeros((1, 0), dtype=np.float32)
        if "dec_c" in self.in_names:
            feed["dec_c"] = self.dec_c.reshape(1, -1).astype(np.float32) if self.dec_c.size else np.zeros((1, 0), dtype=np.float32)
        if "tfa_c" in self.in_names:
            feed["tfa_c"] = self.tfa_c.reshape(1, -1).astype(np.float32) if self.tfa_c.size else np.zeros((1, 0), dtype=np.float32)
        if "inter_c" in self.in_names:
            feed["inter_c"] = self.inter_c.reshape(1, -1).astype(np.float32) if self.inter_c.size else np.zeros((1, 0), dtype=np.float32)
        outs = self.sess.run(self.out_names, feed)
        out_dict = dict(zip(self.out_names, outs))
        enhanced = out_dict.get("enhanced_spec")
        if enhanced is not None:
            enhanced = enhanced.reshape(-1).astype(np.float32)
        else:
            enhanced = spec
        # update caches
        for k, arr in [("enc_c_out", "enc_c"), ("dec_c_out", "dec_c"), ("tfa_c_out", "tfa_c"), ("inter_c_out", "inter_c")]:
            if k in out_dict and arr in self.in_names:
                v = out_dict[k].reshape(-1).astype(np.float32)
                if arr == "enc_c":
                    self.enc_c = v
                elif arr == "dec_c":
                    self.dec_c = v
                elif arr == "tfa_c":
                    self.tfa_c = v
                elif arr == "inter_c":
                    self.inter_c = v
        return self._time_from_spec(enhanced)

    def reset(self):
        self.enc_c = np.zeros_like(self.enc_c)
        self.dec_c = np.zeros_like(self.dec_c)
        self.tfa_c = np.zeros_like(self.tfa_c)
        self.inter_c = np.zeros_like(self.inter_c)
        self.input_history = np.zeros(NFFT - HOP, dtype=np.float32)
        self.ola_acc = np.zeros(NFFT, dtype=np.float32)
        self.win_sum = np.zeros(NFFT, dtype=np.float32)
