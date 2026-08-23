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

# theme_colors.py
"""统一主题颜色管理模块（单一深色主题）。

PureVox 桌面端只有一种外观：墨黑深色。颜色集中定义在此，
其他模块通过便捷函数获取。

用法：
    from theme_colors import current_palette, current_colors

    pal = current_palette()   # QPalette 角色颜色
    c = current_colors()      # 组件颜色
"""

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ═══════════════════════════════════════════════════════════════
#  QPalette 角色颜色（墨黑深色，唯一主题）
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PaletteDef:
    """QPalette 角色颜色定义。"""

    window: QColor
    window_text: QColor
    base: QColor
    alternate_base: QColor
    text: QColor
    button: QColor
    button_text: QColor
    bright_text: QColor
    placeholder_text: QColor
    tooltip_base: QColor
    tooltip_text: QColor
    highlight: QColor
    highlighted_text: QColor
    link: QColor
    mid: QColor
    dark: QColor

    def apply_to(self, pal: QPalette) -> None:
        """将本定义写入一个 QPalette。

        只覆盖基础明暗角色，Highlight/Link 由 apply_theme_palette
        按系统 accent 覆写。
        """
        pal.setColor(QPalette.ColorRole.Window, self.window)
        pal.setColor(QPalette.ColorRole.WindowText, self.window_text)
        pal.setColor(QPalette.ColorRole.Base, self.base)
        pal.setColor(QPalette.ColorRole.AlternateBase, self.alternate_base)
        pal.setColor(QPalette.ColorRole.Text, self.text)
        pal.setColor(QPalette.ColorRole.Button, self.button)
        pal.setColor(QPalette.ColorRole.ButtonText, self.button_text)
        pal.setColor(QPalette.ColorRole.BrightText, self.bright_text)
        pal.setColor(QPalette.ColorRole.PlaceholderText, self.placeholder_text)
        pal.setColor(QPalette.ColorRole.ToolTipBase, self.tooltip_base)
        pal.setColor(QPalette.ColorRole.ToolTipText, self.tooltip_text)
        pal.setColor(QPalette.ColorRole.Mid, self.mid)
        pal.setColor(QPalette.ColorRole.Dark, self.dark)


PALETTE = PaletteDef(
    window=QColor(0x20, 0x20, 0x20),
    window_text=QColor(0xF0, 0xF0, 0xF0),
    base=QColor(0x1A, 0x1A, 0x1A),
    alternate_base=QColor(0x2A, 0x2A, 0x2A),
    text=QColor(0xF0, 0xF0, 0xF0),
    button=QColor(0x2D, 0x2D, 0x2D),
    button_text=QColor(0xF0, 0xF0, 0xF0),
    bright_text=QColor(0xFF, 0x00, 0x00),
    placeholder_text=QColor(0x99, 0x99, 0x99),
    tooltip_base=QColor(0x2D, 0x2D, 0x2D),
    tooltip_text=QColor(0xF0, 0xF0, 0xF0),
    highlight=QColor(0x60, 0xCD, 0xFF),   # 兜底 accent；运行时被系统 accent 覆写
    highlighted_text=QColor(0x00, 0x00, 0x00),
    link=QColor(0x60, 0xCD, 0xFF),
    mid=QColor(0x55, 0x55, 0x55),
    dark=QColor(0x3A, 0x3A, 0x3A),
)


# ═══════════════════════════════════════════════════════════════
#  组件级颜色（按钮、图表、控件等）
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ThemeColors:
    """各组件使用的颜色。"""

    # ── 胶囊控件 SegmentedControl ──
    segment_btn_fg: str          # 未选中按钮前景
    segment_sep: str             # 按钮间分隔线

    # ── VU 表未点亮色 ──
    vu_unlit_green: tuple        # (r, g, b)
    vu_unlit_yellow: tuple
    vu_unlit_red: tuple

    # ── 记录按钮 ──
    record_btn_bg: str
    record_btn_countdown_text: str
    record_btn_progress_fill: str

    # ── 启动/停止按钮 ──
    start_btn_bg: str
    start_btn_hover: str
    stop_btn_bg: str
    stop_btn_hover: str

    # ── EQ 曲线 ──
    eq_grid: str                 # 网格线
    eq_text_secondary: str       # 次要文字（频率标签）

    # ── 频谱直方图 ──
    spec_grid: str               # 网格线
    spec_text_c: str             # 次要文字
    spec_bar_out: str            # 输出柱
    spec_bar_more: str           # 更多语音柱
    spec_bar_less: str           # 更少噪声柱

    # ── 关于对话框 ──
    about_link: str              # 链接/高亮文字色
    about_close_btn_bg: str
    about_close_btn_hover: str


COLORS = ThemeColors(
    segment_btn_fg="#aaaaaa",
    segment_sep="#555555",
    vu_unlit_green=(0, 61, 20),
    vu_unlit_yellow=(61, 61, 0),
    vu_unlit_red=(61, 10, 0),
    record_btn_bg="#424242",
    record_btn_countdown_text="#ff5252",
    record_btn_progress_fill="#f44336",
    start_btn_bg="#4caf50",
    start_btn_hover="#388e3c",
    stop_btn_bg="#f44336",
    stop_btn_hover="#d32f2f",
    eq_grid="#3a3a50",
    eq_text_secondary="#666688",
    spec_grid="#3a3a50",
    spec_text_c="#666688",
    spec_bar_out="#66bb6a",
    spec_bar_more="#616161",
    spec_bar_less="#b0bec5",
    about_link="#60CDFF",
    about_close_btn_bg="#60CDFF",
    about_close_btn_hover="#4DB8E8",
)


# ═══════════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════════

def current_palette() -> PaletteDef:
    """返回主题的 QPalette 定义。"""
    return PALETTE


def current_colors() -> ThemeColors:
    """返回组件颜色定义。"""
    return COLORS


def apply_theme_palette(app: QApplication) -> None:
    """应用主题调色板；Highlight/Link 跟随系统 accent（读不到用兜底色）。"""
    from pvplatform.system import system_accent_color

    pal = app.palette()
    PALETTE.apply_to(pal)
    try:
        accent = system_accent_color()
    except Exception:
        accent = None
    if accent:
        pal.setColor(QPalette.ColorRole.Highlight, accent)
        pal.setColor(QPalette.ColorRole.Link, accent)
    app.setPalette(pal)
