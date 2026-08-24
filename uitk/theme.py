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

"""uitk 主题令牌（纯 tk，无 Qt）——墨黑深色主题的唯一颜色来源。

与 theme_colors.py 的 Qt 调色板保持同源色值；tk 组件一律通过
本模块取色，禁止在组件里写死十六进制颜色。
"""

import sys

# ── 基础面（与 theme_colors.PALETTE 同源）──
WINDOW      = "#202020"   # 窗口底
BASE        = "#1a1a1a"   # 输入区/列表底
ALT_BASE    = "#2a2a2a"   # 交替行/卡片
BUTTON      = "#2d2d2d"   # 按钮常态底
DARK        = "#3a3a3a"   # 按钮悬停底
MID         = "#555555"   # 分隔线/边框

# ── 前景 ──
TEXT        = "#f0f0f0"
TEXT_DIM    = "#999999"   # 占位/次要文字
TEXT_FAINT  = "#666688"

# ── 状态色 ──
START_BG    = "#4caf50"
START_HOVER = "#388e3c"
STOP_BG     = "#f44336"
STOP_HOVER  = "#d32f2f"

ACCENT_FALLBACK = "#60cdff"

# 运行时被系统 accent 覆写（读不到用兜底）
ACCENT = ACCENT_FALLBACK


def refresh_accent() -> None:
    """读取系统 accent 色写入 ACCENT；失败保持兜底。

    pvplatform 返回的可能是 Qt QColor（PySide6 版接口），统一转 #rrggbb。
    """
    global ACCENT
    try:
        if sys.platform.startswith("win"):
            from pvplatform.system import system_accent_color
            accent = system_accent_color()
            if accent:
                if not isinstance(accent, str):
                    accent = accent.name()
                if isinstance(accent, str) and accent.startswith("#"):
                    ACCENT = accent
    except Exception:
        pass


def hover(bg: str) -> str:
    """返回某底色的悬停变体：状态色用各自 hover，中性色提亮一档。"""
    return {
        START_BG: START_HOVER,
        STOP_BG: STOP_HOVER,
    }.get(bg, DARK)
