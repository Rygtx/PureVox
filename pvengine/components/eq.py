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

"""EQ 组件——61 段 peaking biquad 级联（RBJ 系数，Q=√2）。

IIR 递推用 scipy.signal.lfilter（C 实现速度），zi 状态跨帧连续；
全部增益为 0 时组件旁路（对齐原 C eq_active_ 行为）。
"""

import numpy as np
from scipy.signal import lfilter

from pvengine.context import FrameContext, SAMPLE_RATE
from pvengine.stages.base import Stage

EQ_BANDS = 61
EQ_Q = 1.414

EQ_FREQS = (
    20.0, 22.4, 25.0, 28.0, 31.5, 35.5, 40.0, 45.0, 50.0, 56.0,
    63.0, 71.0, 80.0, 90.0, 100.0, 112.0, 125.0, 140.0, 160.0, 180.0,
    200.0, 224.0, 250.0, 280.0, 315.0, 355.0, 400.0, 450.0, 500.0, 560.0,
    630.0, 710.0, 800.0, 900.0, 1000.0, 1120.0, 1250.0, 1400.0, 1600.0, 1800.0,
    2000.0, 2240.0, 2500.0, 2800.0, 3150.0, 3550.0, 4000.0, 4500.0, 5000.0, 5600.0,
    6300.0, 7100.0, 8000.0, 9000.0, 10000.0, 11200.0, 12500.0, 14000.0, 16000.0, 18000.0,
    20000.0,
)


def _peaking_eq(freq: float, gain_db: float, q: float, fs: float):
    """RBJ peaking EQ 双二阶系数，返回 (b0,b1,b2,a1,a2)（a0 归一化）。"""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * freq / fs
    cos_w0 = np.cos(w0)
    sin_w0 = np.sin(w0)
    alpha = sin_w0 / (2.0 * q)
    a0 = 1.0 + alpha / a
    return ((1.0 + alpha * a) / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha * a) / a0,
            (-2.0 * cos_w0) / a0,
            (1.0 - alpha / a) / a0)


class EqStage(Stage):
    name = "eq"

    def __init__(self, sample_rate: float = SAMPLE_RATE):
        super().__init__()
        self.fs = sample_rate
        self.active = False
        self._coeffs = [_peaking_eq(f, 0.0, EQ_Q, sample_rate) for f in EQ_FREQS]
        self._zi = [np.zeros(2) for _ in range(EQ_BANDS)]

    def set_gains(self, gains) -> None:
        """设置 61 段增益（dB）；全零即旁路。"""
        any_nonzero = False
        for i in range(EQ_BANDS):
            g = float(gains[i]) if i < len(gains) else 0.0
            if g != 0.0:
                any_nonzero = True
            self._coeffs[i] = _peaking_eq(EQ_FREQS[i], g, EQ_Q, self.fs)
        self.active = any_nonzero

    def process(self, frame, ctx: FrameContext):
        if not self.active:
            return frame
        y = frame.astype(np.float64)
        for i in range(EQ_BANDS):
            b0, b1, b2, a1, a2 = self._coeffs[i]
            y, self._zi[i] = lfilter([b0, b1, b2], [1.0, a1, a2], y, zi=self._zi[i])
        return y.astype(np.float32)

    def reset(self):
        self._zi = [np.zeros(2) for _ in range(EQ_BANDS)]
