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

"""频域/滤波类音效：卷积混响 / 移相器 / 自动哇音 / 激励器 / 电话声效。

IIR 递推统一走 scipy.signal.lfilter（C 速度 + zi 跨帧状态）；
混响用 FFT 卷积（重叠保留），脉冲响应按参数实时生成。
"""

import numpy as np
from scipy.signal import lfilter, fftconvolve, butter

from pvengine.components.fx.base import Effect


class ReverbEffect(Effect):
    """卷积混响：指数衰减噪声 IR，FFT 重叠保留流式卷积。"""

    NAME = "reverb"
    LABEL = "混响 Reverb"
    PARAMS = {
        "decay_s": ("衰减 s", 0.2, 4.0, 1.5, 0.1),
        "mix": ("湿量", 0.0, 1.0, 0.25, 0.05),
        "predelay_ms": ("预延迟 ms", 0.0, 120.0, 20.0, 5.0),
        "damping": ("阻尼", 0.0, 1.0, 0.4, 0.05),
        "regen": ("再生亮度", 0.0, 1.0, 0.5, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._ir = None
        self._tail = None

    def on_params_changed(self):
        self._ir = None   # 触发重建（惰性）

    def _build_ir(self):
        rng = np.random.default_rng(48)
        sr = 48000
        n = int(self.params["decay_s"] * sr)
        t = np.arange(n) / sr
        ir = rng.standard_normal(n)
        # 阻尼：一阶低通（逐点 EMA 向量化近似）
        damping = float(np.clip(self.params["damping"], 0.0, 0.95))
        alpha = 1.0 - damping * 0.9 + 0.05
        ir = np.cumsum(ir * (1 - alpha)) if False else lfilter([alpha], [1, -(1 - alpha)], ir)
        ir *= np.exp(-t / max(self.params["decay_s"] / 6.0, 1e-3))
        # 再生亮度：抬高中晚段能量
        regen = self.params["regen"]
        ir[int(sr * 0.02):] *= (1.0 + regen * 2.0)
        pre = int(self.params["predelay_ms"] * 48.0)
        ir = np.concatenate([np.zeros(pre), ir])
        peak = np.max(np.abs(ir))
        if peak > 0:
            ir /= np.sqrt(np.sum(ir ** 2)) * 2.0   # 能量归一（湿声不过载）
        self._ir = ir.astype(np.float32)

    def process(self, frame, ctx):
        if self._ir is None:
            self._build_ir()
            self._tail = np.zeros(len(self._ir) - 1, dtype=np.float32)
        wet_full = fftconvolve(frame, self._ir).astype(np.float32)
        out = wet_full[:len(frame)] + self._tail[:len(frame)]
        new_tail_len = len(wet_full) - len(frame)
        tail = wet_full[len(frame):]
        self._tail = np.zeros(len(self._ir) - 1, dtype=np.float32)
        self._tail[:len(tail)] = tail
        mix = np.float32(self.params["mix"])
        return frame * (1.0 - mix) + out * mix

    def reset(self):
        self._tail = None if self._ir is None else \
            np.zeros(len(self._ir) - 1, dtype=np.float32)


class PhaserEffect(Effect):
    """移相器：LFO 调制的一阶全通级联（6 级）+ 干湿混合。
    全通系数每帧更新一次（帧粒度足够平滑）。"""

    NAME = "phaser"
    LABEL = "移相器 Phaser"
    PARAMS = {
        "rate_hz": ("速率 Hz", 0.05, 8.0, 0.5, 0.05),
        "depth": ("深度", 0.0, 1.0, 0.7, 0.05),
        "mix": ("湿量", 0.0, 1.0, 0.5, 0.05),
        "stages": ("级数", 2.0, 8.0, 6.0, 2.0),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._phase = 0.0
        self._zi = [np.zeros(1) for _ in range(8)]

    def process(self, frame, ctx):
        n = len(frame)
        t = (np.arange(n) + self._phase) / 48000.0
        self._phase += n
        lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * self.params["rate_hz"] * t)
        f_min, f_max = 300.0, 3000.0
        f = f_min * (f_max / f_min) ** (lfo * self.params["depth"])
        a_t = (1.0 - np.pi * f.mean() / 48000.0) / (1.0 + np.pi * f.mean() / 48000.0)
        stages = int(self.params["stages"])
        y = frame.astype(np.float64)
        for i in range(stages):
            y, self._zi[i] = lfilter([a_t, 1.0], [1.0, a_t], y, zi=self._zi[i])
        wet = y.astype(np.float32)
        mix = np.float32(self.params["mix"])
        return (frame + wet * mix) * np.float32(0.5 + 0.5 * (1 - mix))

    def reset(self):
        self._zi = [np.zeros(1) for _ in range(8)]


class AutoWahEffect(Effect):
    """自动哇音：包络控制 peaking 滤波器中心频率（包络每帧更新）。"""

    NAME = "autowah"
    LABEL = "自动哇音"
    PARAMS = {
        "sensitivity": ("灵敏度", 0.1, 10.0, 2.5, 0.1),
        "min_hz": ("最低 Hz", 150.0, 500.0, 250.0, 25.0),
        "max_hz": ("最高 Hz", 800.0, 4000.0, 1800.0, 100.0),
        "q": ("Q 值", 1.0, 12.0, 5.0, 0.5),
        "mix": ("湿量", 0.0, 1.0, 1.0, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._zi = np.zeros(2)
        self._env = 0.0

    def _coeffs(self, fc):
        import math
        q = self.params["q"]
        a = 10.0 ** (18.0 / 40.0)          # 固定 +18dB 峰形增益
        w0 = 2.0 * math.pi * min(fc, 20000.0) / 48000.0
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        a0 = 1.0 + alpha / a
        return ((1.0 + alpha * a) / a0,
                (-2.0 * cw) / a0,
                (1.0 - alpha * a) / a0,
                (-2.0 * cw) / a0,
                (1.0 - alpha / a) / a0)

    def process(self, frame, ctx):
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        # 包络 EMA（帧粒度）
        alpha = 1.0 - np.exp(-1.0 / (30.0 * 0.001 * 48000.0 / len(frame) / 21.0))
        self._env += alpha * (rms - self._env)
        norm = min(self._env * self.params["sensitivity"], 1.0)
        fc = self.params["min_hz"] * (self.params["max_hz"] / self.params["min_hz"]) ** norm
        b0, b1, b2, a1, a2 = self._coeffs(fc)
        wet, self._zi = lfilter([b0, b1, b2], [1.0, a1, a2],
                                frame.astype(np.float64), zi=self._zi)
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix) + wet.astype(np.float32) * mix)

    def reset(self):
        self._zi = np.zeros(2)
        self._env = 0.0


class ExciterEffect(Effect):
    """激励器：高通段谐波饱和后回加，提升空气感与齿音光泽。"""

    NAME = "exciter"
    LABEL = "激励器 Exciter"
    PARAMS = {
        "crossover_hz": ("分频 Hz", 2000.0, 9000.0, 4500.0, 250.0),
        "amount": ("谐波量", 0.0, 1.0, 0.35, 0.05),
        "mix": ("混合", 0.0, 1.0, 0.5, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._b_hp = None
        self._a_hp = None
        self._zi = np.zeros(2)
        self._rebuild()

    def on_params_changed(self):
        self._rebuild()

    def _rebuild(self):
        from scipy.signal import butter as _butter
        sos_order = 2
        self._b_hp, self._a_hp = _butter(sos_order,
                                         self.params["crossover_hz"] / 24000.0,
                                         btype="highpass")

    def process(self, frame, ctx):
        hp, self._zi = lfilter(self._b_hp, self._a_hp,
                               frame.astype(np.float64), zi=self._zi)
        amount = self.params["amount"]
        harm = np.tanh(hp * (2.0 + amount * 6.0))
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix * 0.3) +
                (harm * amount).astype(np.float32) * mix).astype(np.float32)

    def reset(self):
        self._zi = np.zeros(2)


class TelephoneEffect(Effect):
    """电话声效：窄带通 300–3400Hz + 软失真 + 轻比特压碎。"""

    NAME = "telephone"
    LABEL = "电话声效"
    PARAMS = {
        "distort": ("失真", 0.0, 1.0, 0.5, 0.05),
        "crush_bits": ("位深", 8.0, 16.0, 11.0, 1.0),
        "mix": ("湿量", 0.0, 1.0, 1.0, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        from scipy.signal import butter as _butter
        self._bp = _butter(2, [320.0 / 24000.0, 3300.0 / 24000.0], btype="bandpass")
        self._zi = np.zeros(4)

    def process(self, frame, ctx):
        wet, self._zi = lfilter(self._bp[0], self._bp[1],
                                frame.astype(np.float64), zi=self._zi)
        d = 1.0 + self.params["distort"] * 8.0
        wet = np.tanh(wet * d) / np.tanh(d)
        bits = 2.0 ** (self.params["crush_bits"] - 1.0)
        wet = np.round(wet * bits) / bits
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix) + wet.astype(np.float32) * mix)

    def reset(self):
        self._zi = np.zeros(4)
