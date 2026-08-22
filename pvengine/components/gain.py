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

"""前置增益 / AGC 组件。

AgcController 是原 C AgcController 的逐常量移植：
目标 -20 dBFS、增益限幅 ±30 dB、静音门 -45 dBFS、RMS 地板 -60 dBFS、
死区 ±0.5 dB、attack 10ms / release 150ms、静音衰减 0.5^dt、
RMS EMA 200ms、静音尾 15 帧。
"""

import math
import numpy as np

from pvengine.context import FrameContext, SAMPLE_RATE, HOP_LENGTH
from pvengine.stages.base import Stage


class AgcController:
    _SILENT_TAIL_FRAMES = 15

    def __init__(self, target_dbfs: float = -20.0, call_interval_ms: float = 10.0):
        self.target_dbfs = target_dbfs
        self.target_linear = 10.0 ** (target_dbfs / 20.0)
        self.gain_min = 10.0 ** (-30.0 / 20.0)
        self.gain_max = 10.0 ** (30.0 / 20.0)
        self.silence_thr = 10.0 ** (-45.0 / 20.0)
        self.rms_floor = 10.0 ** (-60.0 / 20.0)
        self.smoothed_gain = 1.0
        self.rms_ema = 0.0
        self.initialized = False
        self.enabled = False
        self.voice_active = False
        self.silent_tail = 0
        dt = call_interval_ms / 1000.0
        self.attack_alpha = 1.0 - math.exp(-dt / 0.010)
        self.release_alpha = 1.0 - math.exp(-dt / 0.150)
        self.decay_factor = 0.5 ** dt
        self.dead_zone = 10.0 ** (0.5 / 20.0)
        self.rms_alpha = 1.0 - math.exp(-dt / 0.200)

    def reset(self):
        self.smoothed_gain = 1.0
        self.rms_ema = 0.0
        self.initialized = False
        self.voice_active = False
        self.silent_tail = 0

    def set_enabled(self, enabled: bool, initial_gain_db: float = 0.0):
        if enabled and not self.enabled:
            self.smoothed_gain = 10.0 ** (initial_gain_db / 20.0)
            self.reset()
            self.smoothed_gain = 10.0 ** (initial_gain_db / 20.0)
        self.enabled = enabled

    def update_rms(self, rms_linear: float):
        is_voice = rms_linear > self.silence_thr
        if is_voice:
            self.silent_tail = 0
            self.voice_active = True
        else:
            self.silent_tail += 1
            if self.silent_tail >= self._SILENT_TAIL_FRAMES:
                self.voice_active = False
        if not self.voice_active or rms_linear <= self.silence_thr:
            return
        if self.rms_ema == 0.0:
            self.rms_ema = rms_linear
        else:
            self.rms_ema = self.rms_alpha * rms_linear + (1.0 - self.rms_alpha) * self.rms_ema

    def tick(self) -> float:
        """每帧取一次当前增益。"""
        if not self.enabled:
            return 1.0
        if not self.voice_active:
            if self.smoothed_gain > 1.0:
                self.smoothed_gain *= self.decay_factor
                if self.smoothed_gain < 1.0:
                    self.smoothed_gain = 1.0
            return self.smoothed_gain
        if self.rms_ema == 0.0:
            return self.smoothed_gain
        rms = max(self.rms_ema, self.rms_floor)
        target_gain = self.target_linear / rms
        target_gain = min(max(target_gain, self.gain_min), self.gain_max)
        if not self.initialized:
            self.initialized = True
            self.smoothed_gain = target_gain
        else:
            ratio = target_gain / self.smoothed_gain
            if 1.0 / self.dead_zone < ratio < self.dead_zone:
                return self.smoothed_gain
            alpha = self.attack_alpha if target_gain < self.smoothed_gain else self.release_alpha
            self.smoothed_gain = alpha * target_gain + (1.0 - alpha) * self.smoothed_gain
        return self.smoothed_gain

    @property
    def gain_db(self) -> float:
        g = max(self.smoothed_gain, 1e-10)
        return 20.0 * math.log10(g)


class GainStage(Stage):
    """链首增益：AGC 启用时用 AGC 动态增益，否则用固定 pre-gain。"""

    name = "gain"

    def __init__(self, pre_gain_db: float = 0.0):
        super().__init__()
        self.pre_gain = 10.0 ** (pre_gain_db / 20.0)
        self.agc = AgcController()

    def set_pre_gain_db(self, db: float):
        self.pre_gain = 10.0 ** (db / 20.0)

    def set_agc_enabled(self, enabled: bool, initial_gain_db: float = 0.0):
        self.agc.set_enabled(enabled, initial_gain_db)

    def process(self, frame, ctx: FrameContext):
        g = self.agc.tick() if self.agc.enabled else self.pre_gain
        frame *= np.float32(g)
        return frame


class AgcMeterStage(Stage):
    """链尾 AGC 测量：对输出帧测 RMS 喂给 AGC（不修改音频）。"""

    name = "agc_meter"

    def __init__(self, controller: AgcController):
        super().__init__()
        self.agc = controller

    def process(self, frame, ctx: FrameContext):
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        self.agc.update_rms(rms)
        return frame
