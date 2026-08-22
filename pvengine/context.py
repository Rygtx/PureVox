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

"""组件间数据契约：处理模式常量与帧上下文。

纯 Python 音频引擎的数据流约定：
- 音频帧一律 numpy.float32 一维数组，单声道 48kHz；
- 处理 hop 固定 1024 样本（约 21.3ms）；
- 每个组件实现 Stage 接口，按链路顺序消费/产出帧。
"""

from dataclasses import dataclass, field
import numpy as np

SAMPLE_RATE = 48000
HOP_LENGTH = 1024
NFFT = 2048
FREQ = NFFT // 2 + 1          # 1025

# ── 处理模式（与旧 aimic.MODE_* 数值一致）──
MODE_PASSTHROUGH = 0
MODE_DENOISE = 1
MODE_AEC = 2
MODE_TSE = 3


@dataclass
class FrameContext:
    """一帧音频的处理上下文（同一帧在整条链路中共享同一 ctx）。"""
    mode: int = MODE_PASSTHROUGH
    # far-end 参考信号（AEC 用）：设备原始采样率的一维数组或 None；
    # 由 AEC 组件自行负责重采样到 48kHz
    far: np.ndarray | None = None
    far_sample_rate: int = SAMPLE_RATE
    # 组件可写入的旁路数据袋（如录制抽头、调试信息），避免污染接口
    extras: dict = field(default_factory=dict)
