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

"""FxChain——用户自定义音效链 Stage。

链配置为可 JSON 化的 list[dict]：
    [{"type": "reverb", "enabled": true, "params": {...}}, ...]
UI 编辑后经 AudioProcessor.set_fx_chain() 整体替换（重建实例），
运行时单参微调走 update_fx_param(idx, key, value)（不重建、不断流状态）。
"""

import numpy as np

from pvengine.context import FrameContext
from pvengine.stages.base import Stage
from pvengine.components.fx import create_effect


class FxChainStage(Stage):
    name = "fx_chain"

    def __init__(self, chain_config: list | None = None):
        super().__init__()
        self.chain_enabled = True
        self.effects = []          # [(effect_instance, enabled)]
        if chain_config:
            self.rebuild(chain_config)

    def rebuild(self, chain_config: list | None):
        """整体替换音效链（UI 结构变化：添加/删除/排序/改类型）。"""
        self.effects = []
        for item in (chain_config or []):
            eff = create_effect(str(item.get("type", "")), item.get("params"))
            if eff is not None:
                self.effects.append((eff, bool(item.get("enabled", True))))

    def update_param(self, index: int, key: str, value):
        """运行时单参更新（保留流式状态）。参数变更钩子由 effect 自己处理。"""
        if 0 <= index < len(self.effects):
            self.effects[index][0].set_params({key: value})

    def set_effect_enabled(self, index: int, enabled: bool):
        if 0 <= index < len(self.effects):
            self.effects[index] = (self.effects[index][0], bool(enabled))

    def to_config(self) -> list:
        return [{"type": e.NAME, "enabled": en, "params": dict(e.params)}
                for e, en in self.effects]

    def process(self, frame: np.ndarray, ctx: FrameContext) -> np.ndarray:
        if not self.chain_enabled:
            return frame
        for eff, enabled in self.effects:
            if enabled:
                frame = eff.process(frame.astype(np.float32, copy=False), ctx)
        return frame

    def reset(self):
        for eff, _en in self.effects:
            eff.reset()
