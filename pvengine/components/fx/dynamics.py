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

"""动态类音效：限幅器 / 噪声门 / 颤音。"""

import numpy as np

from pvengine.components.fx.base import Effect, db_to_lin


class LimiterEffect(Effect):
    """砖墙限幅器：帧峰值超阈值时平滑压回，release 控制恢复速度。"""

    NAME = "limiter"
    LABEL = "限幅器"
    PARAMS = {
        "threshold_db": ("阈值 dB", -24.0, 0.0, -1.0, 0.5),
        "release_ms": ("释放 ms", 10.0, 500.0, 60.0, 5.0),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._gain = 1.0

    def process(self, frame, ctx):
        thr = db_to_lin(self.params["threshold_db"])
        peak = float(np.max(np.abs(frame))) if len(frame) else 0.0
        target = 1.0 if peak <= thr else thr / max(peak, 1e-9)
        # 攻击瞬时（防过冲），释放按指数平滑
        alpha = 1.0 - np.exp(-1.0 / (self.params["release_ms"] * 0.001 * 48000.0))
        self._gain = target if target < self._gain else \
            self._gain + alpha * (target - self._gain)
        return frame * np.float32(self._gain)

    def reset(self):
        self._gain = 1.0


class GateEffect(Effect):
    """噪声门：RMS 低于阈值时平滑关断，含保持时间。"""

    NAME = "gate"
    LABEL = "噪声门"
    PARAMS = {
        "threshold_db": ("门限 dBFS", -80.0, -20.0, -50.0, 1.0),
        "attack_ms": ("开启 ms", 0.5, 50.0, 2.0, 0.5),
        "hold_ms": ("保持 ms", 0.0, 300.0, 40.0, 5.0),
        "release_ms": ("关闭 ms", 5.0, 500.0, 80.0, 5.0),
        "range_db": ("衰减深度 dB", -60.0, 0.0, -40.0, 1.0),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._env = 0.0
        self._hold = 0

    def process(self, frame, ctx):
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        thr = db_to_lin(self.params["threshold_db"])
        open_ = rms > thr
        if open_:
            self._hold = int(self.params["hold_ms"] * 48)  # 帧≈21ms，粗粒度保持
        elif self._hold > 0:
            self._hold -= 1
            open_ = True
        floor = db_to_lin(self.params["range_db"])
        a_atk = 1.0 - np.exp(-1.0 / (self.params["attack_ms"] * 0.001 * 48000.0))
        a_rel = 1.0 - np.exp(-1.0 / (self.params["release_ms"] * 0.001 * 48000.0))
        target = 1.0 if open_ else floor
        alpha = a_atk if target > self._env else a_rel
        self._env += alpha * (target - self._env)
        return frame * np.float32(self._env)

    def reset(self):
        self._env = 0.0
        self._hold = 0


class TremoloEffect(Effect):
    """颤音：LFO 幅度调制。"""

    NAME = "tremolo"
    LABEL = "颤音 Tremolo"
    PARAMS = {
        "rate_hz": ("速率 Hz", 0.1, 20.0, 4.0, 0.1),
        "depth": ("深度", 0.0, 1.0, 0.6, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._phase = 0.0

    def process(self, frame, ctx):
        n = len(frame)
        t = (np.arange(n) + self._phase) / 48000.0
        lfo = np.cos(2.0 * np.pi * self.params["rate_hz"] * t)
        gain = 1.0 - self.params["depth"] * 0.5 * (1.0 - lfo)
        self._phase = (self._phase + n) % (48000.0 / max(self.params["rate_hz"], 1e-6))
        return frame * gain.astype(np.float32)

    def reset(self):
        self._phase = 0.0
