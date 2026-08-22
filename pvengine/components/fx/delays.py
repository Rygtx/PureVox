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

"""延迟类音效：延迟回声 / 合唱 / 镶边。全部整帧向量化（分数延迟线性插值）。"""

import numpy as np

from pvengine.components.fx.base import Effect


def _frac_delay(buf: np.ndarray, positions: np.ndarray) -> np.ndarray:
    """从环形缓冲按浮点位置取样（线性插值）。positions 为各样本的读位置。"""
    n = len(buf)
    i0 = np.clip(np.floor(positions).astype(np.int64), 0, n - 1)
    i1 = (i0 + 1) % n
    frac = (positions - i0).astype(np.float32)
    return buf[i0] * (1.0 - frac) + buf[i1] * frac


class DelayEffect(Effect):
    """回声：延迟时间 / 反馈 / 湿量，经典反馈延迟线。"""

    NAME = "delay"
    LABEL = "延迟回声"
    PARAMS = {
        "time_ms": ("延迟 ms", 20.0, 1000.0, 250.0, 10.0),
        "feedback": ("反馈", 0.0, 0.9, 0.35, 0.05),
        "mix": ("湿量", 0.0, 1.0, 0.3, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._buf = np.zeros(48000 + 1024, dtype=np.float32)
        self._w = 0

    def _delay_len(self):
        return int(self.params["time_ms"] * 48.0)

    def process(self, frame, ctx):
        dl = self._delay_len()
        idx = (np.arange(len(frame)) - dl - self._w) % len(self._buf)
        wet = self._buf[idx]
        out = frame * (1.0 - self.params["mix"]) + wet * np.float32(self.params["mix"])
        # 写入：干声 + 反馈湿声（反馈路径）
        write = frame + wet * np.float32(self.params["feedback"])
        for i, v in enumerate(write):
            self._buf[(self._w + i) % len(self._buf)] = v
        self._w = (self._w + len(frame)) % len(self._buf)
        return out.astype(np.float32)

    def reset(self):
        self._buf[:] = 0.0
        self._w = 0


class ChorusEffect(Effect):
    """合唱：多条 LFO 调制的分数延迟线叠加，产生多声部展宽感。"""

    NAME = "chorus"
    LABEL = "合唱 Chorus"
    PARAMS = {
        "rate_hz": ("速率 Hz", 0.1, 8.0, 1.2, 0.1),
        "depth_ms": ("深度 ms", 0.5, 12.0, 4.0, 0.5),
        "mix": ("湿量", 0.0, 1.0, 0.5, 0.05),
        "voices": ("声部数", 2.0, 4.0, 3.0, 1.0),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._buf = np.zeros(48000 // 4, dtype=np.float32)   # 250ms 环形缓冲
        self._w = 0
        self._phase = 0.0

    def process(self, frame, ctx):
        n = len(frame)
        buf = self._buf
        # 先把本帧写入缓冲（供读取），再计算调制位置
        end = self._w + n
        if end <= len(buf):
            buf[self._w:end] = frame
        else:
            first = len(buf) - self._w
            buf[self._w:] = frame[:first]
            buf[:end - len(buf)] = frame[first:]
        voices = int(self.params["voices"])
        mix = np.float32(self.params["mix"])
        depth = self.params["depth_ms"] * 48.0
        base = 15.0 * 48.0  # 基准延迟 ~15ms
        t = (np.arange(n) + self._phase) / 48000.0
        wet = np.zeros(n, dtype=np.float32)
        for v in range(voices):
            lfo = np.sin(2.0 * np.pi * self.params["rate_hz"] * t + v * 2.094)
            pos = (self._w + np.arange(n)) - (base + depth * lfo)
            pos = np.mod(pos, len(buf))
            wet += _frac_delay(buf, pos) / voices
        self._phase += n
        self._w = end % len(buf)
        return (frame * (1.0 - mix * 0.5) + wet * mix).astype(np.float32)

    def reset(self):
        self._buf[:] = 0.0
        self._w = 0
        self._phase = 0.0


class FlangerEffect(Effect):
    """镶边：短延迟（~ms 级）+ 反馈的调制梳状滤波。"""

    NAME = "flanger"
    LABEL = "镶边 Flanger"
    PARAMS = {
        "rate_hz": ("速率 Hz", 0.05, 5.0, 0.4, 0.05),
        "depth_ms": ("深度 ms", 0.1, 5.0, 1.5, 0.1),
        "feedback": ("反馈", 0.0, 0.9, 0.5, 0.05),
        "mix": ("湿量", 0.0, 1.0, 0.5, 0.05),
    }

    def __init__(self, params=None):
        super().__init__(params)
        self._buf = np.zeros(2048, dtype=np.float32)
        self._w = 0
        self._phase = 0.0

    def process(self, frame, ctx):
        n = len(frame)
        buf = self._buf
        t = (np.arange(n) + self._phase) / 48000.0
        lfo = np.sin(2.0 * np.pi * self.params["rate_hz"] * t)
        delay = (self.params["depth_ms"] * 48.0) * (0.5 + 0.5 * lfo) + 1.0
        pos = np.mod(self._w + np.arange(n) - delay, len(buf))
        wet = _frac_delay(buf, pos)
        fb = float(self.params["feedback"])
        inp = frame + wet * np.float32(fb)
        end = self._w + n
        if end <= len(buf):
            buf[self._w:end] = inp
        else:
            first = len(buf) - self._w
            buf[self._w:] = inp[:first]
            buf[:end - len(buf)] = inp[first:]
        self._phase += n
        self._w = end % len(buf)
        mix = np.float32(self.params["mix"])
        return (frame * (1.0 - mix * 0.5) + wet * mix).astype(np.float32)

    def reset(self):
        self._buf[:] = 0.0
        self._w = 0
        self._phase = 0.0
