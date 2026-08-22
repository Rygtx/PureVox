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

"""共享 STFT 处理器（TSE 路径用）：Hann² 窗、hop 1024、首帧输出零。"""

import numpy as np

from pvengine.context import NFFT, HOP_LENGTH, FREQ
from pvengine.dsp.core import hann_window


class StftProcessor:
    def __init__(self):
        self.window = hann_window(NFFT)
        self.reset()

    def reset(self):
        self.history = np.zeros(NFFT - HOP_LENGTH, dtype=np.float32)
        self.ola = np.zeros(NFFT, dtype=np.float32)
        self.win_sum = np.zeros(NFFT, dtype=np.float32)
        self.primed = False

    def forward(self, block: np.ndarray) -> np.ndarray:
        """时域 1024 → planar 频谱 2050（[re×1025 | im×1025]）。"""
        x = np.concatenate([self.history, block]) * self.window
        self.history = block.astype(np.float32).copy()
        spec = np.fft.rfft(x, n=NFFT)
        out = np.empty(2 * FREQ, dtype=np.float32)
        out[:FREQ] = spec.real
        out[FREQ:] = spec.imag
        out[0] = spec[0].real          # DC
        out[FREQ - 1] = spec[FREQ - 1].real
        out[FREQ] = 0.0                # DC 虚部
        out[2 * FREQ - 1] = 0.0        # Nyquist 虚部
        return out

    def backward(self, spec_planar: np.ndarray) -> np.ndarray:
        """planar 频谱 → 时域 1024（OLA 归一化；首帧输出零）。"""
        cspec = spec_planar[:FREQ] + 1j * spec_planar[FREQ:]
        frame = np.fft.irfft(cspec, n=NFFT).astype(np.float32) * self.window
        self.ola += frame
        self.win_sum += self.window * self.window
        if not self.primed:
            self.primed = True
            out = np.zeros(HOP_LENGTH, dtype=np.float32)
        else:
            norm = self.win_sum[:HOP_LENGTH]
            with np.errstate(divide="ignore", invalid="ignore"):
                scaled = np.where(norm > 1e-6,
                                  self.ola[:HOP_LENGTH] / np.maximum(norm, 1e-30),
                                  self.ola[:HOP_LENGTH])
            out = scaled.astype(np.float32)
        self.ola[:-HOP_LENGTH] = self.ola[HOP_LENGTH:]
        self.ola[-HOP_LENGTH:] = 0.0
        self.win_sum[:-HOP_LENGTH] = self.win_sum[HOP_LENGTH:]
        self.win_sum[-HOP_LENGTH:] = 0.0
        return out
