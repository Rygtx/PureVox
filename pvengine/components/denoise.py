# PureVox — AI 麦克风降噪工具
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
#
# PureVox is licensed under the GNU General Public License v3.0 or
# later (GPL-3.0-or-later).  See LICENSE for details.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# The built-in AI models are NOT covered by the GPL; they are the
# property of a2heng and may only be used with PureVox under
# authorization.  See MODEL-LICENSE.md for details.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Denoise 组件——v9 模型（2048 FFT / hop 1024 / sqrt-Hann）流式推理。

模型契约：spec [1,1025,1,2]（interleaved 复数谱）+ enc_c/dec_c/tfa_c/inter_c
四个 RNN 状态输入；输出 enhanced_spec + 对应 *_out 状态。
状态维度优先从会话元数据读取，动态维回退历史常量。
"""

import numpy as np

from pvengine.context import NFFT, HOP_LENGTH, FREQ
from pvengine.dsp.core import make_session, sqrt_hann, state_dim
from pvengine.stages.base import Stage


class DenoiseEngine:
    def __init__(self, model_path: str):
        self.sess = make_session(model_path)
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]
        dims = {"enc_c": (77106, "enc_c_out"), "dec_c": (53862, "dec_c_out"),
                "tfa_c": (1056, "tfa_c_out"), "inter_c": (1024, "inter_c_out")}
        self.states = {k: np.zeros((1, state_dim(self.sess, k, fb)), dtype=np.float32)
                       for k, (fb, _o) in dims.items()}
        self.window = sqrt_hann(NFFT)
        self.history = np.zeros(NFFT - HOP_LENGTH, dtype=np.float32)
        self.ola = np.zeros(NFFT, dtype=np.float32)
        self.win_sum = np.zeros(NFFT, dtype=np.float32)
        # 预热 3 帧静音（对齐原 C denoise_new，让 RNN 状态收敛到静音基线）
        for _ in range(3):
            self.process_chunk(np.zeros(HOP_LENGTH, dtype=np.float32))

    def _analyze(self, block: np.ndarray) -> np.ndarray:
        x = np.concatenate([self.history, block]) * self.window
        self.history = block.astype(np.float32).copy()
        spec = np.fft.rfft(x, n=NFFT)
        packed = np.empty(2 * FREQ, dtype=np.float32)
        packed[0::2] = spec.real
        packed[1::2] = spec.imag
        packed[1] = 0.0            # DC 虚部
        packed[2 * FREQ - 1] = 0.0  # Nyquist 虚部
        return packed

    def _run_onnx(self, spec: np.ndarray) -> None:
        feed = {}
        for name in self.in_names:
            if name == "spec":
                feed[name] = spec.reshape(1, FREQ, 1, 2)
            elif name in self.states:
                feed[name] = self.states[name]
        outs = self.sess.run(self.out_names, feed)
        od = dict(zip(self.out_names, outs))
        enh = od.get("enhanced_spec")
        if enh is not None:
            spec[:] = np.asarray(enh, dtype=np.float32).reshape(-1)[:2 * FREQ]
        for key in self.states:
            v = od.get(key + "_out")
            if v is not None:
                flat = np.asarray(v, dtype=np.float32).reshape(-1)
                n = min(flat.size, self.states[key].size)
                self.states[key].reshape(-1)[:n] = flat[:n]

    def _synth(self, spec: np.ndarray) -> np.ndarray:
        cspec = spec[0::2] + 1j * spec[1::2]
        frame = np.fft.irfft(cspec, n=NFFT).astype(np.float32) * self.window
        self.ola += frame
        self.win_sum += self.window * self.window
        norm = self.win_sum[:HOP_LENGTH]
        out = np.where(norm > 1e-6,
                       self.ola[:HOP_LENGTH] / np.maximum(norm, 1e-30),
                       self.ola[:HOP_LENGTH]).astype(np.float32)
        self.ola[:-HOP_LENGTH] = self.ola[HOP_LENGTH:]
        self.ola[-HOP_LENGTH:] = 0.0
        self.win_sum[:-HOP_LENGTH] = self.win_sum[HOP_LENGTH:]
        self.win_sum[-HOP_LENGTH:] = 0.0
        return out

    def process_chunk(self, block: np.ndarray) -> np.ndarray:
        if len(block) != HOP_LENGTH:
            raise ValueError("denoise: chunk must be 1024 samples")
        spec = self._analyze(block)
        self._run_onnx(spec)
        return self._synth(spec)

    def reset(self):
        for k in self.states:
            self.states[k][:] = 0.0
        self.history[:] = 0.0
        self.ola[:] = 0.0
        self.win_sum[:] = 0.0

    def release(self):
        self.sess = None


class DenoiseStage(Stage):
    """降噪模式组件：在 DENOISE/AEC/TSE 模式下生效（AEC/TSE 先降噪再处理）。"""

    name = "denoise"
    active_modes = frozenset({1, 2, 3})

    def __init__(self, model_path: str):
        super().__init__()
        self.engine = DenoiseEngine(model_path)

    def process(self, frame, ctx):
        return self.engine.process_chunk(frame)

    def reset(self):
        self.engine.reset()

    def release(self):
        self.engine.release()
