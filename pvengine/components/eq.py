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

"""EQ 组件——61 段 1/6 倍频程图示均衡器（标准做法）。

- 频点为 ISO 266 1/6 倍频程标称中心频率（20 Hz ~ 20 kHz，相邻比 2^(1/6)）；
- 每段一个 RBJ peaking biquad（audio-eq-cookbook 系式）；
- Q 与频点栅格带宽匹配：Q = √(2^N)/(2^N − 1)，N = 1/6 倍频程 → ≈8.65。
  这是图示 EQ 的标准取法——每段 -3 dB 带宽≈本段栅格宽度，
  调一段只影响自己附近的频带，不向邻段大面积串扰；
- 全零增益整体旁路；运行时跳过零增益段（恒等滤波器，跳过不改输出）；
- IIR 递推走 scipy.signal.lfilter（C 实现），zi 状态跨帧连续。

response_at() 是全链频响的唯一权威实现：引擎与 UI 曲线共用同一份系数与 Q。
"""

import math

import numpy as np
from scipy.signal import lfilter

from pvengine.context import FrameContext, SAMPLE_RATE
from pvengine.stages.base import Stage

EQ_BANDS = 61

# 标准 1/6 倍频程带宽匹配 Q：Q = √(2^N)/(2^N − 1)，N = 1/6 ≈ 8.651
_EQ_OCTAVES = 1.0 / 6.0
EQ_Q = (2.0 ** (_EQ_OCTAVES / 2.0)) / (2.0 ** _EQ_OCTAVES - 1.0)

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


def response_at(freq: float, gains, fs: float = SAMPLE_RATE) -> float:
    """全部峰值段在 freq 处的总响应（dB）。UI 曲线绘制与本文件共用。"""
    total_db = 0.0
    w = 2.0 * math.pi * freq / fs
    c, s = math.cos(w), math.sin(w)
    c2, s2 = math.cos(2.0 * w), math.sin(2.0 * w)
    for i, g in enumerate(gains):
        if i >= EQ_BANDS or abs(float(g)) < 1e-9:
            continue
        b0, b1, b2, a1, a2 = _peaking_eq(EQ_FREQS[i], float(g), EQ_Q, fs)
        nr = b0 + b1 * c + b2 * c2
        ni = -(b1 * s + b2 * s2)
        dr = 1.0 + a1 * c + a2 * c2
        di = -(a1 * s + a2 * s2)
        total_db += 20.0 * math.log10(math.hypot(nr, ni) / math.hypot(dr, di))
    return total_db


class EqStage(Stage):
    name = "eq"

    def __init__(self, sample_rate: float = SAMPLE_RATE):
        super().__init__()
        self.fs = sample_rate
        self.active = False
        self._active_idx: tuple[int, ...] = ()
        self._coeffs = [_peaking_eq(f, 0.0, EQ_Q, sample_rate) for f in EQ_FREQS]
        self._zi = [np.zeros(2) for _ in range(EQ_BANDS)]

    def set_gains(self, gains) -> None:
        """设置 61 段增益（dB）；全零即旁路。新激活段清零滤波器状态。"""
        active = []
        prev_active = self._active_idx
        for i in range(EQ_BANDS):
            g = float(gains[i]) if i < len(gains) else 0.0
            self._coeffs[i] = _peaking_eq(EQ_FREQS[i], g, EQ_Q, self.fs)
            if g != 0.0:
                active.append(i)
        self._active_idx = tuple(active)
        for i in self._active_idx:
            if i not in prev_active:
                self._zi[i][:] = 0.0
        self.active = bool(active)

    def process(self, frame, ctx: FrameContext):
        if not self.active:
            return frame
        y = frame.astype(np.float64)
        for i in self._active_idx:
            b0, b1, b2, a1, a2 = self._coeffs[i]
            y, self._zi[i] = lfilter([b0, b1, b2], [1.0, a1, a2], y, zi=self._zi[i])
        return y.astype(np.float32)

    def reset(self):
        self._zi = [np.zeros(2) for _ in range(EQ_BANDS)]
