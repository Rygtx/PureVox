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

"""音效注册表：type 名 → Effect 类。UI 据此生成添加菜单与参数控件。"""

from pvengine.components.fx.base import Effect
from pvengine.components.fx.dynamics import LimiterEffect, GateEffect, TremoloEffect
from pvengine.components.fx.delays import DelayEffect, ChorusEffect, FlangerEffect
from pvengine.components.fx.filters import (ReverbEffect, PhaserEffect, AutoWahEffect,
                                            ExciterEffect, TelephoneEffect)
from pvengine.components.fx.saturate import DistortionEffect, BitCrushEffect

# 顺序即 UI「添加效果」下拉的展示顺序
EFFECT_TYPES: dict[str, type[Effect]] = {
    cls.NAME: cls
    for cls in (
        ReverbEffect,
        DelayEffect,
        ChorusEffect,
        FlangerEffect,
        PhaserEffect,
        TremoloEffect,
        AutoWahEffect,
        DistortionEffect,
        BitCrushEffect,
        ExciterEffect,
        GateEffect,
        LimiterEffect,
        TelephoneEffect,
    )
}


def create_effect(effect_type: str, params: dict | None = None) -> Effect | None:
    """按注册名实例化音效；未知类型返回 None（向前兼容旧配置）。"""
    cls = EFFECT_TYPES.get(effect_type)
    if cls is None:
        return None
    return cls(params)
