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

"""AEC 组件——aec9 模型（回声消除）。

契约：mic/far 各自维护 2048 滑动历史，sqrt-Hann 谱 planar [re|im]，
模型输入 mic_frame/far_frame [1,2,1,1025] + 一组流式状态；
far-end 非恒 48k 时组件内部用 Resampler 重采样（对齐原 C 行为：
ratio = 48000/far_sr，1024 静音预热，取不满一帧则本帧直通）。
"""

import numpy as np

from pvengine.context import NFFT, HOP_LENGTH, FREQ
from pvengine.dsp.core import make_session, sqrt_hann, state_dim
from pvengine.dsp.resampler import Resampler
from pvengine.stages.base import Stage

_STATE_NAMES = (
    "res_enc_conv", "res_enc_tfa", "mic_enc_conv", "mic_enc_tfa",
    "deep_enc_tfa", "dec_conv", "dec_tfa", "inter",
)
_FALLBACK_DIMS = {
    "res_enc_conv": 108544, "res_enc_tfa": 248,
    "mic_enc_conv": 108544, "mic_enc_tfa": 248,
    "deep_enc_tfa": 432, "dec_conv": 10752, "dec_tfa": 496, "inter": 6144,
}
_PREV_SIZE = 256


class AecEngine:
    def __init__(self, model_path: str):
        self.sess = make_session(model_path)
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]
        self.window = sqrt_hann(NFFT)   # 注意：AEC 窗无 1e-10 偏置差异可忽略，统一复用
        self.states = {}
        for name in _STATE_NAMES:
            if name in self.in_names:
                dim = state_dim(self.sess, name, _FALLBACK_DIMS[name])
                self.states[name] = np.zeros((1, dim), dtype=np.float32)
        for prev in ("res_prev1", "res_prev2", "mic_prev1", "mic_prev2"):
            if prev in self.in_names:
                self.states[prev] = np.zeros((1, 1, 1, _PREV_SIZE), dtype=np.float32)
        if "delay_buf" in self.in_names:
            shape = [d if isinstance(d, int) and d > 0 else 1
                     for d in next(i.shape for i in self.sess.get_inputs()
                                   if i.name == "delay_buf")]
            self.states["delay_buf"] = np.zeros(shape, dtype=np.float32)
        self.mic_history = np.zeros(NFFT, dtype=np.float32)
        self.far_history = np.zeros(NFFT, dtype=np.float32)
        self.ola = np.zeros(NFFT, dtype=np.float32)
        self.win_sum = np.zeros(NFFT, dtype=np.float32)

    @staticmethod
    def _planar_spec(x_nfft: np.ndarray, window: np.ndarray) -> np.ndarray:
        spec = np.fft.rfft(x_nfft * window, n=NFFT)
        out = np.empty(2 * FREQ, dtype=np.float32)
        out[:FREQ] = spec.real
        out[FREQ:] = spec.imag
        out[FREQ] = 0.0
        out[2 * FREQ - 1] = 0.0
        return out

    def process_frame(self, mic: np.ndarray, far: np.ndarray) -> np.ndarray:
        """mic/far 各 1024 样本（48kHz）→ 输出 1024。"""
        self.mic_history[:-HOP_LENGTH] = self.mic_history[HOP_LENGTH:]
        self.mic_history[-HOP_LENGTH:] = mic
        self.far_history[:-HOP_LENGTH] = self.far_history[HOP_LENGTH:]
        self.far_history[-HOP_LENGTH:] = far

        feed = {}
        for name in self.in_names:
            if name == "mic_frame":
                feed[name] = self._planar_spec(self.mic_history, self.window).reshape(1, 2, 1, FREQ)
            elif name == "far_frame":
                feed[name] = self._planar_spec(self.far_history, self.window).reshape(1, 2, 1, FREQ)
            elif name in self.states:
                feed[name] = self.states[name]
        outs = self.sess.run(self.out_names, feed)
        od = dict(zip(self.out_names, outs))

        enh = od.get("enhanced_frame")
        cspec = np.empty(FREQ, dtype=np.complex64)
        if enh is not None:
            data = np.asarray(enh, dtype=np.float32).reshape(-1)
            cspec = (data[:FREQ] + 1j * data[FREQ:]).astype(np.complex64)

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

        # 回写状态（输出名可能是原名或 *_o）
        for key, val in self.states.items():
            v = od.get(key + "_o", od.get(key))
            if v is None or key in ("mic_frame", "far_frame"):
                continue
            flat = np.asarray(v, dtype=np.float32).reshape(-1)
            tgt = val.reshape(-1)
            n = min(flat.size, tgt.size)
            tgt[:n] = flat[:n]
        return out

    def reset(self):
        for v in self.states.values():
            v[:] = 0.0
        self.mic_history[:] = 0.0
        self.far_history[:] = 0.0
        self.ola[:] = 0.0
        self.win_sum[:] = 0.0

    def release(self):
        self.sess = None


class AecStage(Stage):
    name = "aec"
    active_modes = frozenset({2})

    def __init__(self, model_path: str):
        super().__init__()
        self.engine = AecEngine(model_path)
        self._far_resampler: Resampler | None = None
        self.far_sample_rate = 48000

    def set_far_sample_rate(self, sr: int):
        sr = int(sr) if sr and sr > 0 else 48000
        self.far_sample_rate = sr
        if sr != 48000:
            self._far_resampler = Resampler()
            # 1024 静音预热（对齐原 C：让插值历史先建立）
            self._far_resampler.process(np.zeros(HOP_LENGTH, dtype=np.float32),
                                        48000.0 / sr)
        else:
            self._far_resampler = None

    def process(self, frame, ctx):
        far = ctx.far
        far_sr = ctx.far_sample_rate
        if far is None or len(far) == 0:
            return frame
        if self._far_resampler is not None and far_sr != 48000:
            got = self._far_resampler.process(far, 48000.0 / float(far_sr))
            if len(got) < HOP_LENGTH:
                return frame
            far_ref = np.asarray(got[:HOP_LENGTH], dtype=np.float32)
        elif len(far) >= HOP_LENGTH:
            far_ref = np.asarray(far[:HOP_LENGTH], dtype=np.float32)
        else:
            return frame
        return self.engine.process_frame(frame, far_ref)

    def reset(self):
        self.engine.reset()

    def release(self):
        self.engine.release()
