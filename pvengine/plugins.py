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

"""统一插件注册表——整条音频管线全部由这里的插件构成。

核心处理（增益/AGC/噪声门/EQ/压缩器/AI 三件套）与 FX 音效共用同一插件接口，
UI 的右侧面板按 CATALOG 顺序展示「添加」菜单；管线 = 用户排列的插件实例序列。

engine_cache：AudioProcessor 持有的 dict，AI 插件共享模型 Stage，
链重建不重复加载模型。
"""

from pvengine.components.core_plugins import (
    GainPlugin, AgcPlugin, GatePlugin, EqPlugin, CompressorPlugin,
    DenoiserPlugin, EchoCancelPlugin, TsePlugin,
)
from pvengine.components.fx import EFFECT_TYPES as _FX_TYPES

# 目录顺序：核心在前（信号流惯例顺序），FX 在后
CATALOG: list[type] = [
    GainPlugin,
    DenoiserPlugin,
    EchoCancelPlugin,
    TsePlugin,
    GatePlugin,
    AgcPlugin,
    EqPlugin,
    CompressorPlugin,
]
CATALOG += list(_FX_TYPES.values())

PLUGIN_TYPES: dict[str, type] = {cls.NAME: cls for cls in CATALOG}

# 特殊 UI 钩子：这些类型在行内渲染额外控件（由 ui 层判断类型实现）
SPECIAL_ROWS = {"eq", "tse"}

# ── UI 层级元数据 ──
# toggle  = 仅开/关（无参数）
# inline  = 行内参数滑杆（默认，按 PARAMS 自动生成）
# expand  = 行内控制 + 「展开」按钮弹出独立 UI 对话框
UI_TIERS = {
    "denoiser": "toggle",
    "echo_cancel": "inline",   # 行内含 far 端扬声器设备下拉
    "eq": "expand",      # 展开：EQ 曲线编辑器
    "tse": "expand",     # 展开：参考音频录制对话框
}

# 展开对话框标题（ui 层据此路由到对应编辑器）
EXPAND_TITLES = {
    "eq": "均衡器",
    "tse": "参考音频",
}


def ui_tier(ptype: str) -> str:
    """插件 UI 层级：未显式声明时，有参数=inline，无参数=toggle。"""
    if ptype in UI_TIERS:
        return UI_TIERS[ptype]
    cls = PLUGIN_TYPES.get(ptype)
    return "inline" if (cls and cls.PARAMS) else "toggle"

# 全新配置的默认链（对齐旧默认行为：降噪开启，其余按需添加）
DEFAULT_CHAIN = [
    {"type": "gain", "enabled": True, "params": {}},
    {"type": "denoiser", "enabled": True, "params": {}},
]


def create_plugin(ptype: str, params: dict | None = None,
                  stage_cache: dict | None = None):
    """按注册名实例化插件；未知类型返回 None（向前兼容旧配置）。

    stage_cache：AudioProcessor 持有的 AI Stage 缓存，链重建不重复加载模型。
    """
    cls = PLUGIN_TYPES.get(ptype)
    if cls is None:
        return None
    try:
        return cls(params, stage_cache=stage_cache)
    except TypeError:
        return cls(params)


def label_of(ptype: str) -> str:
    cls = PLUGIN_TYPES.get(ptype)
    return cls.LABEL if cls else ptype
