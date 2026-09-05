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

"""行级 AEC 合成测试（无硬件；模型会话用桩代替，不加载 onnx）：
python tests/test_aec_rows.py

验证：
1. FarTap：48k 直通连续、非 48k 重采样后恒 480 帧；
2. AecRow：mic 直通恒满帧、far 经 FarTap 直达桩会话
   （桩回显 far 即看到 far 信号）。
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import pvengine.aec_row as aec_row_mod
from pvengine.aec_row import AecRow
from pvengine.dsp.far_sync import FarTap

HOP = 480


class _FakeEngine:
    """桩会话：输出 = mic（验证增益与通路），cache 原样返回。"""

    def new_state(self):
        return np.zeros((1, 8), dtype=np.float32)

    def process_frame(self, mic, far, cache):
        assert len(mic) == HOP and len(far) == HOP
        return np.asarray(mic, dtype=np.float32), np.asarray(
            cache, dtype=np.float32)


def _sine(n, phase=0.0, freq=220.0, sr=48000.0):
    return [math.sin(phase + i * 2 * math.pi * freq / sr) for i in range(n)]


def test_far_tap_passthrough():
    tap = FarTap(48000)
    for _ in range(12):
        tap.push(_sine(HOP))
    out = tap.pull()
    assert len(out) == HOP
    d = tap.diag()
    assert d["conceals"] == 0 and d["drops"] == 0
    print("  FarTap 48k 直通恒帧、无 conceal/drop  OK")


def test_far_tap_resample():
    tap = FarTap(44100)
    for _ in range(14):
        tap.push(_sine(441, sr=44100.0))
    out = tap.pull()
    assert len(out) == HOP
    print("  FarTap 44.1k 重采样恒 480 帧  OK")


def test_aec_row_passthrough():
    """mic 直通：桩回显 mic，输出恒满帧且与输入一致。"""
    aec_row_mod.get_shared_engine = lambda p: _FakeEngine()
    row = AecRow("dummy.onnx", far_sample_rate=48000)
    for _ in range(12):
        row.push_far([0.5] * HOP)
    mic = [0.1] * HOP
    out = row.process_mic(mic)
    assert len(out) == HOP
    assert abs(float(out[240]) - 0.1) < 1e-6, out[240]
    assert len(row.last_far) == HOP
    print("  AecRow mic 直通、恒满帧  OK")


def test_aec_row_far_direct():
    """far 直达：桩会话改回显 far，切 far 信号即在输出看到。"""
    class _EchoFar:
        def new_state(self):
            return np.zeros((1, 8), dtype=np.float32)

        def process_frame(self, mic, far, cache):
            return np.asarray(far, dtype=np.float32), cache

    aec_row_mod.get_shared_engine = lambda p: _EchoFar()
    row = AecRow("dummy.onnx", far_sample_rate=48000, far_gain_db=0.0)
    for _ in range(12):
        row.push_far([0.3] * HOP)
    out = row.process_mic([0.0] * HOP)
    assert abs(float(out[240]) - 0.3) < 1e-3, out[240]
    print("  AecRow far 直达  OK")


def test_aec_row_far_gain():
    """参考音量：只缩放进模型的 far 帧（桩回显 far 即看到缩放）。"""
    class _EchoFar:
        def new_state(self):
            return np.zeros((1, 8), dtype=np.float32)

        def process_frame(self, mic, far, cache):
            return np.asarray(far, dtype=np.float32), cache

    aec_row_mod.get_shared_engine = lambda p: _EchoFar()
    row = AecRow("dummy.onnx", far_sample_rate=48000, far_gain_db=-6.0)
    for _ in range(12):
        row.push_far([0.4] * HOP)
    out = row.process_mic([0.0] * HOP)
    assert abs(float(out[240]) - 0.4 * 0.5011872) < 1e-3, out[240]
    row.set_far_gain_db(0.0)
    out = row.process_mic([0.0] * HOP)
    assert abs(float(out[240]) - 0.4) < 1e-3, out[240]
    print("  AecRow 参考音量只缩放 far  OK")


if __name__ == "__main__":
    print("行级 AEC 合成测试:")
    test_far_tap_passthrough()
    test_far_tap_resample()
    test_aec_row_passthrough()
    test_aec_row_far_direct()
    test_aec_row_far_gain()
    print("全部通过")
