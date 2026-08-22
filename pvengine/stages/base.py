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

"""Stage 组件接口——音频链路组件化的唯一契约。

实现一个音频组件只需要：
1. 继承 Stage，实现 process(frame, ctx) -> frame；
2. 可选覆写 reset()（会话重开）与 release()（释放模型等重资源）；
3. 通过 active_modes 声明本组件在哪些处理模式下生效（空集合 = 恒生效）。

满足接口的组件可随意增删、替换、重排，Pipeline 不感知具体实现。
"""

from abc import ABC, abstractmethod
import numpy as np

from pvengine.context import FrameContext


class Stage(ABC):
    """音频处理组件基类。frame 为 float32 一维数组（通常 1024 样本）。"""

    name: str = "stage"
    # 本组件生效的处理模式集合；None = 任何模式都生效
    active_modes: frozenset | None = None

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def accepts(self, ctx: FrameContext) -> bool:
        if not self.enabled:
            return False
        return self.active_modes is None or ctx.mode in self.active_modes

    @abstractmethod
    def process(self, frame: np.ndarray, ctx: FrameContext) -> np.ndarray:
        """处理一帧，返回同长度帧（除非契约允许变长）。"""

    def reset(self) -> None:
        """流式状态复位（模式切换/会话重开时由 Pipeline 调用）。"""

    def release(self) -> None:
        """释放重资源（ONNX 会话等）；调用后本组件不再可用。"""
