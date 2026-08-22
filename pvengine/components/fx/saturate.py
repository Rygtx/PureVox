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

"""失真/饱和与比特粉碎。"""

import numpy as np
from scipy.signal import lfilter

from pvengine.components.fx.base import Effect


class DistortionEffect(Effect):
    """失真/饱和：tanh 软削波 + 前置驱动 + 音色低通 + 干湿混合。
    增益归一化保证提高 drive 不改变响度基准。"""

    NAME = "distortion"
    LABEL = "失真 Distortion"
    PARAMS = {
        "drive_db": ("驱动 dB", 0.0, 36.0, 12.0, 1.0),
        "tone_hz": ("音色 Hz", 1000.0, 16000.0, 6000.0, 500.0),
        "mix": ("混合", 0.0, 1.0, 1.0, 0.05),
        "asym": ("不对称度", 0.0, 1.0, 0.0, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        from scipy.signal import butter as _butter
        self._lp = _butter(2, min(self.params["tone_hz"], 20000.0) / 24000.0)
        self._zi = np.zeros(2)

    def on_params_changed(self):
        from scipy.signal import butter as _butter
        self._lp = _butter(2, min(self.params["tone_hz"], 20000.0) / 24000.0)

    def process(self, frame, ctx):
        drive = 10.0 ** (self.params["drive_db"] / 20.0)
        x = frame.astype(np.float64) * drive
        asym = self.params["asym"]
        # 不对称：正半周增益略高（电子管偶次谐波感）
        if asym > 0.0:
            x = x * (1.0 + asym * 0.5 * (x > 0))
        wet = np.tanh(x) / np.tanh(min(drive, 3.0))
        wet, self._zi = lfilter(self._lp[0], self._lp[1], wet, zi=self._zi)
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix) + wet.astype(np.float32) * mix).astype(np.float32)

    def reset(self):
        self._zi = np.zeros(2)


class BitCrushEffect(Effect):
    """比特粉碎：量化位深 + 采样保持降采样，Lo-Fi 数字味。"""

    NAME = "bitcrush"
    LABEL = "比特粉碎 BitCrush"
    PARAMS = {
        "bits": ("位深", 2.0, 16.0, 8.0, 1.0),
        "downsample": ("降采样倍数", 1.0, 20.0, 4.0, 1.0),
        "mix": ("混合", 0.0, 1.0, 1.0, 0.05),
    }

    def process(self, frame, ctx):
        bits = 2.0 ** (self.params["bits"] - 1.0)
        wet = np.round(frame.astype(np.float64) * bits) / bits
        k = max(int(self.params["downsample"]), 1)
        if k > 1:
            idx = (np.arange(len(wet)) // k) * k
            wet = wet[idx]
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix) + wet.astype(np.float32) * mix).astype(np.float32)
