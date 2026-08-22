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

"""TSE 组件——tse15 模型（目标说话人提取）。

契约：spec_frame [1,2,1,1025] planar + enr_spec [1,2,Te,1025]（参考音频谱）
+ cache_in [319040] → enh_frame + cache_out。
参考音频在 set_reference 时一次性转 STFT 谱缓存（镜像填充 1024 样本）。
"""

import numpy as np

from pvengine.context import NFFT, HOP_LENGTH, FREQ
from pvengine.dsp.core import make_session, hann_window, state_dim
from pvengine.dsp.stft import StftProcessor
from pvengine.stages.base import Stage

_CACHE_TOTAL = 319040


class TseEngine:
    def __init__(self, model_path: str):
        self.sess = make_session(model_path)
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]
        dim = state_dim(self.sess, "cache_in", _CACHE_TOTAL)
        self.cache = np.zeros(max(dim, 1), dtype=np.float32)   # 模型要求 rank-1
        self.window = hann_window(NFFT)
        self.enr: np.ndarray | None = None   # (2, Te, 1025)

    @property
    def has_reference(self) -> bool:
        return self.enr is not None

    def set_reference(self, ref: np.ndarray) -> bool:
        """参考音频（48kHz float）→ 逐帧 STFT 谱缓存。n<2048 拒绝。"""
        ref = np.asarray(ref, dtype=np.float32).reshape(-1)
        n = len(ref)
        if n < NFFT:
            return False
        te = n // HOP_LENGTH + 1
        pad = NFFT // 2
        padded = np.zeros(n + 2 * pad, dtype=np.float32)
        # 两端镜像（跳过首/末样本，对齐原 C）
        k = min(pad, n - 1)
        if k > 0:
            padded[pad - k:pad] = ref[1:k + 1][::-1]
            padded[pad + n:pad + n + k] = ref[n - 1 - k:n - 1][::-1]
        padded[pad:pad + n] = ref
        enr = np.zeros((2, te, FREQ), dtype=np.float32)
        for tt in range(te):
            off = tt * HOP_LENGTH
            spec = np.fft.rfft(padded[off:off + NFFT] * self.window, n=NFFT)
            enr[0, tt] = spec.real
            enr[1, tt] = spec.imag
            enr[1, tt, 0] = 0.0
            enr[1, tt, FREQ - 1] = 0.0
        self.enr = enr
        return True

    def process_spec_freq(self, spec_planar: np.ndarray) -> None:
        """planar 频谱原地增强（无参考时直通）。"""
        if not self.has_reference:
            return
        feed = {}
        for name in self.in_names:
            if name == "spec_frame":
                feed[name] = spec_planar.reshape(1, 2, 1, FREQ).astype(np.float32)
            elif name == "enr_spec":
                feed[name] = self.enr[np.newaxis, ...]
            elif name == "cache_in":
                feed[name] = self.cache
        outs = self.sess.run(self.out_names, feed)
        od = dict(zip(self.out_names, outs))
        enh = od.get("enh_frame")
        if enh is not None:
            spec_planar[:] = np.asarray(enh, dtype=np.float32).reshape(-1)[:2 * FREQ]
        co = od.get("cache_out")
        if co is not None:
            flat = np.asarray(co, dtype=np.float32).reshape(-1)
            n = min(flat.size, self.cache.size)
            self.cache.reshape(-1)[:n] = flat[:n]

    def reset(self):
        self.cache[:] = 0.0

    def release(self):
        self.sess = None


class TseStage(Stage):
    """TSE 模式组件：共享 STFT 前向 → tse15 推理 → 反向 OLA。"""

    name = "tse"
    active_modes = frozenset({3})

    def __init__(self, model_path: str, stft: StftProcessor | None = None):
        super().__init__()
        self.engine = TseEngine(model_path)
        self.stft = stft or StftProcessor()

    @property
    def has_reference(self) -> bool:
        return self.engine.has_reference

    def set_reference(self, ref: np.ndarray) -> bool:
        return self.engine.set_reference(ref)

    def process(self, frame, ctx):
        if not self.engine.has_reference:
            return frame
        spec = self.stft.forward(frame)
        self.engine.process_spec_freq(spec)
        return self.stft.backward(spec)

    def reset(self):
        self.engine.reset()
        self.stft.reset()

    def release(self):
        self.engine.release()
