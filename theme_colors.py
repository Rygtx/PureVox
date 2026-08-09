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
"""统一主题颜色管理模块。
所有深色/浅色主题的颜色值集中定义在此，其他模块通过便捷函数获取当前主题颜色。

用法：
    from theme_colors import get_theme_palette, get_theme_colors, is_dark_current

    dark = is_dark_current()
    pal = get_theme_palette(dark)   # QPalette 角色颜色
    c = get_theme_colors(dark)      # 组件颜色
"""

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ═══════════════════════════════════════════════════════════════
#  QPalette 角色颜色（接近 Windows 11 实际值）
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PaletteDef:
    """一套完整的 QPalette 角色颜色定义。"""

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
        
        只覆盖基础明暗角色，保留系统的 Highlight/Link 等主题色。
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
        # 注意：不覆盖 Highlight / HighlightedText / Link，保留系统主题色


# ---- Windows 11 浅色主题 QPalette ----
PALETTE_LIGHT = PaletteDef(
    window=QColor(0xF3, 0xF3, 0xF3),
    window_text=QColor(0x00, 0x00, 0x00),
    base=QColor(0xFF, 0xFF, 0xFF),
    alternate_base=QColor(0xF5, 0xF5, 0xF5),
    text=QColor(0x00, 0x00, 0x00),
    button=QColor(0xF0, 0xF0, 0xF0),
    button_text=QColor(0x00, 0x00, 0x00),
    bright_text=QColor(0xFF, 0x00, 0x00),
    placeholder_text=QColor(0x70, 0x70, 0x70),
    tooltip_base=QColor(0xFF, 0xFF, 0xFF),
    tooltip_text=QColor(0x00, 0x00, 0x00),
    highlight=QColor(0x00, 0x78, 0xD4),   # Win11 accent blue
    highlighted_text=QColor(0xFF, 0xFF, 0xFF),
    link=QColor(0x00, 0x78, 0xD4),
    mid=QColor(0xCC, 0xCC, 0xCC),
    dark=QColor(0xA0, 0xA0, 0xA0),
)

# ---- Windows 11 深色主题 QPalette ----
PALETTE_DARK = PaletteDef(
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
    highlight=QColor(0x60, 0xCD, 0xFF),   # Win11 dark accent blue
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
    """各组件使用的颜色，按深色/浅色分别定义。"""

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


# ---- 浅色组件颜色 ----
COLORS_LIGHT = ThemeColors(
    segment_btn_fg="#555555",
    segment_sep="#cccccc",
    vu_unlit_green=(204, 240, 204),
    vu_unlit_yellow=(240, 240, 204),
    vu_unlit_red=(240, 204, 204),
    record_btn_bg="#424242",
    record_btn_countdown_text="#ff5252",
    record_btn_progress_fill="#f44336",
    start_btn_bg="#4caf50",
    start_btn_hover="#388e3c",
    stop_btn_bg="#f44336",
    stop_btn_hover="#d32f2f",
    eq_grid="#d0d0d0",
    eq_text_secondary="#999999",
    spec_grid="#d0d0d0",
    spec_text_c="#999999",
    spec_bar_out="#4caf50",
    spec_bar_more="#e0e0e0",
    spec_bar_less="#90a4ae",
    about_link="#0078D4",
    about_close_btn_bg="#0078D4",
    about_close_btn_hover="#1565c0",
)

# ---- 深色组件颜色 ----
COLORS_DARK = ThemeColors(
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

def is_dark_current() -> bool:
    """检测当前生效的主题是否为深色（优先检测调色板明暗）。"""
    app = QApplication.instance()
    if app:
        pal = app.palette()
        return pal.window().color().lightness() < 128
    return QApplication.palette().window().color().lightness() < 128


def get_theme_palette(dark: bool) -> PaletteDef:
    """返回深色或浅色的 QPalette 定义。"""
    return PALETTE_DARK if dark else PALETTE_LIGHT


def get_theme_colors(dark: bool) -> ThemeColors:
    """返回深色或浅色的组件颜色定义。"""
    return COLORS_DARK if dark else COLORS_LIGHT


def current_palette() -> PaletteDef:
    """返回当前主题的 QPalette 定义。"""
    return get_theme_palette(is_dark_current())


def current_colors() -> ThemeColors:
    """返回当前主题的组件颜色定义。"""
    return get_theme_colors(is_dark_current())


def apply_theme_palette(app: QApplication, dark: bool) -> None:
    """在系统当前调色板基础上叠加主题明暗定义。
    
    _sync_theme_ui 已先调 setStyle 获取系统最新调色板（含 accent），
    此处在 app.palette() 上覆盖基础明暗，Highlight/Link 保留系统色。
    """
    pal = app.palette()
    get_theme_palette(dark).apply_to(pal)
    app.setPalette(pal)
