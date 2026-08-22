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

"""
PySide6 Main UI
"""

import asyncio
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

# 平台抽象层（win32 依赖仅在 Windows 上延迟导入，避免 Linux 上 import 即崩）
from pvplatform.system import (
    acquire_single_instance, is_autostart, enable_autostart, disable_autostart,
    beep as _sys_beep, open_sound_panel as _sys_open_sound_panel,
    add_firewall_rule as _sys_add_firewall,
    system_accent_color, set_titlebar_theme,
    run_as_admin as _sys_run_as_admin,
)
from pvplatform import IS_WINDOWS, IS_LINUX, IS_MACOS

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QCheckBox,
    QSlider, QSizePolicy, QSystemTrayIcon, QMenu, QButtonGroup,
    QLineEdit, QDialog, QFrame, QScrollArea,
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QRectF, QUrl
from PySide6.QtGui import QIcon, QAction, QFont, QColor, QPalette, QPainter, QPainterPath, QPixmap, QPen, QDesktopServices
from PySide6.QtWidgets import QMessageBox

from audio_processor import (
    get_local_lan_ip, get_device_names, get_device_id,
    API_TYPE_WASAPI, API_TYPE_NETWORK, get_api_name_by_type,
    device_config_suffix,
    get_platform_api_options, default_api_type,
    create_audio_processor, start_audio_stream, HOP_LENGTH,
    load_tse_reference,
    register_tse_audio_hook, _recorder,
    _samples_to_wav_bytes, CFG_REF_WAV_PATH,
)
try:
    import pyaudio  # noqa: E402
except ImportError:
    pyaudio = None  # type: ignore
# pyaudio（PortAudio）仅 Windows/macOS 专用；Linux 全程原生 PipeWire，
# 本模块对 pyaudio 的引用都在 Linux 不可达的 48k 检测分支内。
from config_manager import ConfigManager
from logger import Logger, log, get_logger
from model_config import DENOISE_MODEL, TSE_MODEL, AEC_MODEL

from server.https_server import PureVoxServer


try:
    from _build_version import BUILD_DATE
except ImportError:
    BUILD_DATE = "开发版"

RECORD_DURATION = 10.0

def _api_tooltip() -> str:
    """音频接口下拉框的工具提示（平台感知）。"""
    if sys.platform.startswith("linux"):
        return ("音频接口(API)：\n"
                "PulseAudio — Linux 桌面常用音频服务（PipeWire 兼容），推荐。")
    if sys.platform.startswith("darwin"):
        return "音频接口(API)：\nCore Audio — macOS 原生音频接口。"
    return ("音频接口(API)：\n"
            "WASAPI — Windows 原生低延迟音频接口（默认），\n"
            "         支持共享模式，延迟约 10ms，推荐。\n"
            "MME    — Windows 旧版音频接口，兼容老驱动，\n"
            "         延迟较高（约 100ms），仅当 WASAPI 不可用时使用。")


def _output_tooltip() -> str:
    """输出设备下拉框的工具提示（平台感知）。"""
    if sys.platform.startswith("linux"):
        return ("输出设备 — 降噪后音频的 PipeWire 目标节点\n"
                "默认 purevox_out（PureVox 虚拟麦克风），\n"
                "其它软件选 PureVox 虚拟麦克风（purevox_out.monitor）\n"
                "当麦克风即可收到降噪后的声音；也可改选扬声器节点直接外放。")
    return ("输出设备 — 处理后音频的播放目标\n"
            "不懂怎么设置？选择 CABLE Input，\n"
            "再把 CABLE Output 设为系统默认麦克风，\n"
            "这样任何软件都能用你处理后的声音，\n"
            "当成虚拟麦克风来使用。")


def _jack_default_mic() -> str:
    """Linux 默认输入：第一个物理麦克风节点名。"""
    from pvplatform.audio.pwpipe_client import default_mic_name
    return default_mic_name()


def _jack_default_sink() -> str:
    """Linux 默认输出：PureVox 虚拟麦克风 sink 节点名。"""
    from pvplatform.audio.pwpipe_client import default_sink_name
    return default_sink_name()


def _jack_default_far() -> str:
    """Linux AEC far 兜底：物理扬声器 sink（排除 PureVox 虚拟麦克风）。"""
    from pvplatform.audio.pwpipe_client import speaker_sink_name
    return speaker_sink_name()


def _combo_value(combo) -> str:
    """取下拉框选中值：Linux PipeWire 模式用 userData（真实 node.name），
    其余平台用显示文本。"""
    if IS_LINUX and combo is not None and combo.count():
        d = combo.currentData()
        if d:
            return str(d)
    return combo.currentText() if combo is not None else ""


# 推理后端（自动选择）：实际生效后端 + NPU 未生效原因 → 中文状态文本
_BACKEND_LABELS = {0: "AVX", 1: "SSE", 2: "NPU"}
_BACKEND_REASON_NOTES = {
    0: "",
    1: "（NPU 执行提供程序不可用，已用 CPU 运行）",
    2: "（当前平台无 NPU 执行提供程序，已用 CPU 运行）",
}


def _backend_status_text(eff, reason) -> str:
    """格式化推理后端状态文本（实际生效 + 回退原因）。"""
    name = _BACKEND_LABELS.get(eff, "AVX")
    note = _BACKEND_REASON_NOTES.get(reason, "")
    return f"{name}{note}"


class DebouncedSaver:
    def __init__(self, config: ConfigManager, delay_ms: int = 500):
        self._config = config
        self._delay_ms = delay_ms
        self._timer: Optional[QTimer] = None
        self._pending = False

    def request_save(self):
        self._pending = True
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._do_save)
        self._timer.start(self._delay_ms)

    def save_now(self):
        if self._timer:
            self._timer.stop()
        self._do_save()

    def _do_save(self):
        if self._pending:
            self._pending = False
            self._config.save_config()


@dataclass
class AppState:
    main_panel: Optional['MainPanel'] = None
    model_path: str = ""
    processor: Optional[Any] = None
    processing_thread: Optional[Any] = None
    is_processing: bool = False
    tray_icon: Optional[QSystemTrayIcon] = None
    root: Optional[QMainWindow] = None
    config: Optional[Any] = None

    api_type: int = field(default_factory=default_api_type)
    debounced_saver: Optional[DebouncedSaver] = None
    icon_on_path: str = ""
    icon_off_path: str = ""
    logger: Optional[Logger] = None
    was_processing_before_sleep: bool = False  # 睡眠前是否正在处理
    network_server: Optional[PureVoxServer] = None
    _server_loop: Optional[asyncio.AbstractEventLoop] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key, default=None):
        with self._lock:
            return getattr(self, key, default)

    def set(self, key, value):
        with self._lock:
            if hasattr(self, key):
                setattr(self, key, value)


_state: AppState = AppState()


from theme_colors import is_dark_current, get_theme_palette


def is_dark_theme(config=None) -> bool:
    """检测当前是否为深色主题（优先检测调色板以便手动模式也能正确反映）"""
    return is_dark_current()


def effective_theme_mode(config) -> str:
    """返回当前生效的主题模式：'system' / 'light' / 'dark'"""
    if config is None:
        return "system"
    mode = config.get("theme", "system")
    if mode not in ("system", "light", "dark"):
        return "system"
    return mode


def _get_system_accent_color():
    """读取系统 accent / 主题色（Windows 读注册表；其它平台返回 None）。"""
    return system_accent_color()


def _sync_theme_ui(app: QApplication, config) -> None:
    """统一的主题同步入口 —— 无论系统触发还是用户触发，都走此函数。"""
    mode = effective_theme_mode(config)
    manual = (mode != "system")

    if mode == "system":
        dark = is_dark_current()
    else:
        dark = (mode == "dark")

    # 构建 palette：基础明暗 + 系统 accent
    pal = app.palette()
    get_theme_palette(dark).apply_to(pal)
    accent = _get_system_accent_color()
    if accent:
        pal.setColor(QPalette.Highlight, accent)
    app.setPalette(pal)

    # Re-polish 所有窗口 + DWM 标题栏
    for w in app.topLevelWidgets():
        w.style().unpolish(w)
        w.style().polish(w)
        for child in w.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)
        if manual and hasattr(w, 'winId'):
            _set_titlebar_theme(int(w.winId()), dark)

    # 菜单栏：手动模式用 Qt 渲染（跟随 palette），系统模式用 native
    _refresh_menus(app, manual=manual)


def _refresh_menus(app: QApplication, manual: bool):
    """刷新菜单栏：手动模式 Qt 渲染，系统模式 native。
    
    QSS 中的 palette(...) 引用是动态的，palette 变化后自动生效，
    但 QMenu 是独立弹出窗口需要单独刷新。
    """
    # 菜单栏：手动模式切到 Qt 渲染后，QStyle 绘制 item 和背景用 widget palette，
    # 必须设完整 palette（含 Window 角色控制背景）
    for w in app.topLevelWidgets():
        mb = w.menuBar() if hasattr(w, 'menuBar') else None
        if mb:
            mb.setNativeMenuBar(not manual)
            mb.style().unpolish(mb)
            mb.style().polish(mb)
    # 已弹出的 QMenu 子控件也刷新（QMenu 是独立弹出窗口，不在 topLevelWidgets 中）
    # 逐菜单重设自身 QSS 触发 palette(...) 重解析，再 unpolish/polish 子控件
    for menu in app.allWidgets():
        if isinstance(menu, QMenu):
            ms = menu.styleSheet()
            if ms:
                menu.setStyleSheet(ms)
            menu.update()
            for child in menu.findChildren(QWidget):
                child.style().unpolish(child)
                child.style().polish(child)


def _set_titlebar_theme(hwnd: int, dark: bool) -> None:
    """设置系统标题栏深色/浅色模式（仅 Windows DWM 有效，其它平台空操作）。"""
    set_titlebar_theme(int(hwnd), dark)


# ═══════════════════════════════════════════════════════════════
#  VU 表常量
# ═══════════════════════════════════════════════════════════════

_VU_DB_MIN, _VU_DB_MAX = -60.0, 0.0
_VU_DB_RNG = _VU_DB_MAX - _VU_DB_MIN
_VU_PEAK_FALL = 20.0


class VUBar(QWidget):
    """横向电平条：暗色区域 + 亮色进度条 + 峰值缓慢回落"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self.setMinimumWidth(100)
        self._peak = _VU_DB_MIN
        self._peak_time = 0.0
        self._db = _VU_DB_MIN
        self._t = time.monotonic()
        self._bg_cache = None  # QPixmap 缓存静态背景
        self._cache_size = None
        self._last_painted_db = _VU_DB_MIN - 10.0  # 上次触发重绘的 dB 值

    def update_level(self, samples):
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        peak = max(max(abs(x) for x in samples), 1e-10)
        db = 20.0 * _math.log10(peak)
        self._db = db
        if self._db > self._peak:
            self._peak = self._db
            self._peak_time = now
        elif now - self._peak_time > _VU_PEAK_HOLD:
            self._peak = max(_VU_DB_MIN, self._peak - _VU_PEAK_FALL * dt)
        # 只在大幅变化时触发重绘（省 CPU）
        if abs(db - self._last_painted_db) >= 0.3:
            self._last_painted_db = db
            self.update()

    def update_level_db(self, db):
        """直接从 dBFS 值更新（跳过波形→峰值计算）。"""
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        self._db = db
        if self._db > self._peak:
            self._peak = self._db
            self._peak_time = now
        elif now - self._peak_time > _VU_PEAK_HOLD:
            self._peak = max(_VU_DB_MIN, self._peak - _VU_PEAK_FALL * dt)
        if abs(db - self._last_painted_db) >= 0.3:
            self._last_painted_db = db
            self.update()

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.PaletteChange:
            self._bg_cache = None
            self.update()
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_cache = None  # 尺寸变了，缓存失效

    def _vu_unlit_colors(self):
        """返回未点亮 VU 条的渐变色（深绿、深黄、深红），随主题变化。"""
        from theme_colors import current_colors
        tc = current_colors()
        return (QColor(*tc.vu_unlit_green),
                QColor(*tc.vu_unlit_yellow),
                QColor(*tc.vu_unlit_red))

    def _ensure_bg_cache(self, w, h):
        if self._bg_cache is not None and self._cache_size == (w, h):
            return
        self._bg_cache = QPixmap(w, h)
        self._bg_cache.fill(Qt.transparent)
        self._cache_size = (w, h)
        vu_bg = self.palette().base().color()
        dg, dy, dr = self._vu_unlit_colors()
        p = QPainter(self._bg_cache)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(0, 0, w, h, vu_bg)

        bar_left, bar_right = 6, w - 4
        bar_top, bar_h = 2, 12
        bar_bottom = bar_top + bar_h
        bar_w = bar_right - bar_left

        # 暗色背景条（分区：绿/黄/红）
        g1_r = (-20.0 - _VU_DB_MIN) / _VU_DB_RNG
        g2_r = (-9.0 - _VU_DB_MIN) / _VU_DB_RNG
        x1 = bar_left + int(g1_r * bar_w)
        x2 = bar_left + int(g2_r * bar_w)
        p.fillRect(bar_left, bar_top, x1 - bar_left, bar_h, dg)
        p.fillRect(x1, bar_top, x2 - x1, bar_h, dy)
        p.fillRect(x2, bar_top, bar_right - x2, bar_h, dr)
        p.end()

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w < 40 or h < 16:
            return
        self._ensure_bg_cache(w, h)
        vu_text = self.palette().placeholderText().color()

        bar_left, bar_right = 6, w - 4
        bar_top, bar_h = 2, 12
        bar_bottom = bar_top + bar_h
        bar_w = bar_right - bar_left

        g1_r = (-20.0 - _VU_DB_MIN) / _VU_DB_RNG
        g2_r = (-9.0 - _VU_DB_MIN) / _VU_DB_RNG
        x1 = bar_left + int(g1_r * bar_w)
        x2 = bar_left + int(g2_r * bar_w)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._bg_cache)

        # 动态亮色进度条
        r_now = max(0.0, min(1.0, (self._db - _VU_DB_MIN) / _VU_DB_RNG))
        fill_x = bar_left + int(r_now * bar_w)

        if fill_x > bar_left:
            p.fillRect(bar_left, bar_top, min(fill_x, x1) - bar_left, bar_h, _VU_GREEN)
        if fill_x > x1:
            p.fillRect(x1, bar_top, min(fill_x, x2) - x1, bar_h, _VU_YELLOW)
        if fill_x > x2:
            p.fillRect(x2, bar_top, fill_x - x2, bar_h, _VU_RED)

        # 峰值指示器
        if self._peak > _VU_DB_MIN + 0.5:
            r_peak = max(0.0, min(1.0, (self._peak - _VU_DB_MIN) / _VU_DB_RNG))
            px = int(bar_left + r_peak * bar_w)
            if px > bar_left + 1:
                if r_peak < g1_r:
                    pk_color = _VU_GREEN
                elif r_peak < g2_r:
                    pk_color = _VU_YELLOW
                else:
                    pk_color = _VU_RED
                p.fillRect(QRectF(px - 1, bar_top, 3, bar_h), pk_color)

        # 刻度线和标签
        _tick_font = p.font()
        _tick_font.setPointSize(7)
        p.setFont(_tick_font)
        for tick in _VU_TICKS:
            x = bar_left + max(0.0, min(1.0, (tick - _VU_DB_MIN) / _VU_DB_RNG)) * bar_w
            p.setPen(QPen(vu_text, 0.5))
            p.drawLine(int(x), bar_bottom, int(x), bar_bottom + 3)
            p.setPen(vu_text)
            p.drawText(QRectF(x - 20, bar_bottom, 40, 16),
                       Qt.AlignHCenter | Qt.AlignVCenter, str(tick))

        p.end()


class VUPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._bar = VUBar()
        layout.addWidget(self._bar, 1)

    def update_level(self, samples):
        self._bar.update_level(samples)

    def update_level_db(self, db):
        self._bar.update_level_db(db)


# ═══════════════════════════════════════════════════════════════
#  主面板
# ═══════════════════════════════════════════════════════════════

class PluginRow(QWidget):
    """插件行——三级 UI：
    toggle  仅开关（无参数插件，如 AI 智能降噪）
    inline  开关 + 行内参数滑杆（或特殊控件：AEC 的 far 设备下拉）
    expand  行内控制 + 「展开…」按钮弹出独立 UI（EQ 曲线 / TSE 参考录音）
    """

    changed = Signal(int, str, object)      # (row_index, key, value) 参数微调
    toggled = Signal(int, bool)             # 行内启用/停用
    actionRequested = Signal(str, int, int) # (remove|move, row_index, direction)
    expandRequested = Signal(str)           # 展开独立 UI（携带 ptype）

    def __init__(self, index, plugin_type, label, params_spec, params, enabled,
                 devices=None, parent=None):
        super().__init__(parent)
        from pvengine.plugins import ui_tier
        self.row_index = index
        self.plugin_type = plugin_type
        self.params_spec = params_spec
        self.tier = ui_tier(plugin_type)
        self._devices = list(devices or [])

        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.cb_on = QCheckBox(label)
        self.cb_on.setChecked(enabled)
        self.cb_on.toggled.connect(self._on_toggle)
        head.addWidget(self.cb_on)
        head.addStretch()
        if self.tier == "expand":
            title = {"eq": "均衡器…", "tse": "参考音频…"}.get(plugin_type, "展开…")
            eb = QPushButton(title)
            eb.setFixedHeight(20)
            eb.clicked.connect(lambda: self.expandRequested.emit(self.plugin_type))
            head.addWidget(eb)
        for sym, tip, fn in (("↑", "上移", lambda: self._move(-1)),
                             ("↓", "下移", lambda: self._move(1)),
                             ("✕", "删除", self._remove)):
            b = QPushButton(sym)
            b.setFixedSize(20, 20)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            head.addWidget(b)
        lay.addLayout(head)

        if self.tier == "inline":
            grid = QGridLayout()
            grid.setSpacing(2)
            grid.setColumnStretch(1, 1)
            r = 0
            self._sliders = {}
            if plugin_type == "echo_cancel":
                # 特殊控件：far 端扬声器下拉（回声参考设备）
                gl = QLabel("回声参考设备")
                gl.setStyleSheet("color: palette(mid); font-size: 8pt;")
                grid.addWidget(gl, r, 0)
                self.dev_combo = QComboBox()
                self._fill_devices(params.get("far_device", ""))
                self.dev_combo.currentTextChanged.connect(
                    lambda txt: self.changed.emit(self.row_index, "far_device", txt))
                self.dev_combo.setToolTip(
                    "回声消除需要采集正在出声的扬声器作参考。\n"
                    "选「自动」时使用系统默认物理扬声器。")
                grid.addWidget(self.dev_combo, r, 1, 1, 2)
                r += 1
            for key, (lbl, lo, hi, default, step) in params_spec.items():
                gl = QLabel(lbl)
                gl.setStyleSheet("color: palette(mid); font-size: 8pt;")
                grid.addWidget(gl, r, 0)
                sl = QSlider(Qt.Horizontal)
                sl.setRange(int(lo * 10), int(hi * 10))
                sl.setValue(int(float(params.get(key, default)) * 10))
                val_lbl = QLabel(self._fmt(params.get(key, default)))
                val_lbl.setFixedWidth(46)
                val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

                def _on_slide(v, k=key, vl=val_lbl):
                    real = v / 10.0
                    vl.setText(self._fmt(real))
                    self.changed.emit(self.row_index, k, real)

                sl.valueChanged.connect(_on_slide)
                grid.addWidget(sl, r, 1)
                grid.addWidget(val_lbl, r, 2)
                self._sliders[key] = sl
                r += 1
            lay.addLayout(grid)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    def _fill_devices(self, current: str):
        items = [("自动（默认物理扬声器）", "")] + [(d, d) for d in self._devices]
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for text, data in items:
            self.dev_combo.addItem(text, data)
        idx = self.dev_combo.findData(current)
        self.dev_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.dev_combo.blockSignals(False)

    def set_devices(self, devices):
        """外部刷新输出设备列表（echo_cancel 行的下拉随之更新）。"""
        self._devices = list(devices or [])
        if hasattr(self, "dev_combo"):
            cur = self.dev_combo.currentData()
            self._fill_devices(cur or "")

    def to_params(self) -> dict:
        """行内当前参数（含特殊控件值）。"""
        out = {k: sl.value() / 10.0 for k, sl in self._sliders.items()} \
            if hasattr(self, "_sliders") else {}
        if self.plugin_type == "echo_cancel" and hasattr(self, "dev_combo"):
            out["far_device"] = self.dev_combo.currentData() or ""
        return out

    @staticmethod
    def _fmt(v):
        fv = float(v)
        return f"{fv:.1f}" if fv != int(fv) else f"{int(fv):g}"

    def _on_toggle(self, on):
        self.toggled.emit(self.row_index, on)

    def _remove(self):
        self.actionRequested.emit("remove", self.row_index, 0)

    def _move(self, direction):
        self.actionRequested.emit("move", self.row_index, direction)


class PluginPanel(QWidget):
    """右侧插件面板：全部音频处理以插件行呈现，可添加/删除/排序/实时调参。"""

    chainChanged = Signal(list)

    def __init__(self, config, saver=None, get_processor=None,
                 get_devices=None, logger=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._saver = saver
        self._get_processor = get_processor or (lambda: None)
        self._get_devices = get_devices or (lambda: [])
        self._log = logger or get_logger()

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("处理链")
        title.setStyleSheet("font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        self._add_combo = QComboBox()
        from pvengine.plugins import CATALOG
        for cls in CATALOG:
            self._add_combo.addItem(cls.LABEL, cls.NAME)
        head.addWidget(self._add_combo, 1)
        add_btn = QPushButton("+ 添加")
        add_btn.setFixedHeight(22)
        add_btn.clicked.connect(self._on_add)
        head.addWidget(add_btn)
        clear_btn = QPushButton("清空")
        clear_btn.setFixedHeight(22)
        clear_btn.setToolTip("清空处理链")
        clear_btn.clicked.connect(lambda: self.load_chain([]))
        head.addWidget(clear_btn)
        root.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._rows_lay = QVBoxLayout(body)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        self._rows_lay.addStretch()
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

        self.load_chain(config.get("plugin_chain", []))

    # ── 配置 ↔ UI ──
    def load_chain(self, chain_cfg):
        while self._rows_lay.count() > 1:
            item = self._rows_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        from pvengine.plugins import PLUGIN_TYPES
        self._rows = []
        for item in chain_cfg:
            t = str(item.get("type", ""))
            cls = PLUGIN_TYPES.get(t)
            if not cls:
                continue
            row = PluginRow(len(self._rows), t, cls.LABEL, cls.PARAMS,
                            item.get("params", {}), bool(item.get("enabled", True)),
                            devices=self._get_devices())
            row.changed.connect(self._on_param)
            row.toggled.connect(self._on_row_toggle)
            row.actionRequested.connect(self._on_action)
            row.expandRequested.connect(self._on_expand)
            self._rows.append(row)
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._renumber()

    def refresh_devices(self):
        for r in self._rows:
            r.set_devices(self._get_devices())

    def to_config(self):
        return [{"type": r.plugin_type, "enabled": r.cb_on.isChecked(),
                 "params": r.to_params()} for r in self._rows]

    def _renumber(self):
        for i, r in enumerate(self._rows):
            r.row_index = i

    # ── 变更处理 ──
    def _save_and_apply(self):
        cfg = self.to_config()
        try:
            self._config.set("plugin_chain", cfg)
        except Exception:
            pass
        if self._saver:
            self._saver.schedule()
        proc = self._get_processor()
        if proc is not None:
            try:
                proc.set_plugins(cfg)
            except Exception as e:
                self._log.warn(f"[插件链] 应用失败: {e}")
        self.chainChanged.emit(cfg)

    def _on_param(self, row_index, key, value):
        proc = self._get_processor()
        if proc is not None:
            try:
                proc.update_plugin_param(row_index, key, value)
            except Exception:
                pass
        try:
            self._config.set("plugin_chain", self.to_config())
        except Exception:
            pass
        if self._saver:
            self._saver.schedule()

    def _on_row_toggle(self, row_index, on):
        proc = self._get_processor()
        if proc is not None and 0 <= row_index < len(self._rows):
            try:
                proc.set_plugin_enabled(row_index, on)
            except Exception:
                pass
        self._save_and_apply()

    def _on_add(self):
        t = self._add_combo.currentData()
        if not t:
            return
        cfg = self.to_config()
        from pvengine.plugins import PLUGIN_TYPES
        cls = PLUGIN_TYPES[t]
        cfg.append({"type": t, "enabled": True,
                    "params": {k: spec[3] for k, spec in cls.PARAMS.items()}})
        self.load_chain(cfg)
        self._save_and_apply()

    def _on_action(self, action, row_index, direction):
        cfg = self.to_config()
        if action == "remove":
            del cfg[row_index]
        elif action == "move":
            j = row_index + direction
            if not (0 <= j < len(cfg)):
                return
            cfg[row_index], cfg[j] = cfg[j], cfg[row_index]
        self.load_chain(cfg)
        self._save_and_apply()

    def _on_expand(self, ptype):
        # 由外层接线到具体编辑器（EQ 曲线对话框 / TSE 参考录音弹框）
        if ptype == "tse" and hasattr(self, "_open_tse_dialog"):
            self._open_tse_dialog()
        elif ptype == "eq" and hasattr(self, "_open_eq_editor"):
            self._open_eq_editor()


class MainPanel(QWidget):
    # 仅 Windows 有虚拟声卡概念；Linux/macOS 直接选真实输出设备
    DEFAULT_OUTPUT_DEVICE = "" if not IS_WINDOWS else "CABLE Input"

    def __init__(self, parent, config, debounced_saver=None,
                 on_api_changed=None, on_device_changed=None,
                 logger=None, **kwargs):
        super().__init__(parent)
        self._config = config
        self._saver = debounced_saver
        self._on_api_changed = on_api_changed
        self._on_device_changed = on_device_changed
        self._monitor_changed_cb = None
        self._get_thread = None
        self._log = logger or get_logger()

        self._api_type = default_api_type()
        self._api_opts = get_platform_api_options()

        self._pre_gain = config.get("pre_gain_db", 0.0)

        self._build_ui()
        self._load_config()
        # AGC 初始状态：如果已启用，启动轮询定时器
    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        super().changeEvent(event)

    def _build_ui(self):
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root = QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(4, 4, 4, 4)

        # ── 第4行：设备（扁平） ──
        dev_grid = QGridLayout()
        dev_grid.setSpacing(4)
        dev_grid.setContentsMargins(0, 0, 0, 0)

        dev_grid.setColumnMinimumWidth(0, 56)
        dev_grid.setColumnStretch(1, 1)

        lbl_api = QLabel("音频接口")
        lbl_api.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lbl_api.setToolTip(_api_tooltip())
        dev_grid.addWidget(lbl_api, 0, 0)
        self._api_combo = QComboBox()
        self._api_combo.addItems([o[0] for o in self._api_opts])
        self._api_combo.currentTextChanged.connect(self._handle_api_changed)
        dev_grid.addWidget(self._api_combo, 0, 1)

        self._lbl_in = QLabel("输入设备")
        self._lbl_in.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._lbl_in.setToolTip(
            "输入设备 — 你的物理麦克风\n"
            "选择你正在使用的麦克风，\n"
            "例如 Realtek Audio、USB 麦克风等。")
        dev_grid.addWidget(self._lbl_in, 1, 0)
        self._input_combo = QComboBox()
        self._input_combo.currentTextChanged.connect(lambda: self._on_dev_changed(True))
        dev_grid.addWidget(self._input_combo, 1, 1)
        self._input_url = QLineEdit()
        self._input_url.setPlaceholderText("https://192.168.1.100:59123")
        self._input_url.setToolTip(
            "API网址 — 手机/网页推流到本机的 WebSocket 地址\n"
            "格式: https://本机IP:端口\n"
            "例如: https://192.168.1.100:59123")
        self._input_url.returnPressed.connect(self._on_url_changed)
        self._input_url.hide()
        dev_grid.addWidget(self._input_url, 1, 1)

        lbl_out = QLabel("输出设备")
        lbl_out.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lbl_out.setToolTip(_output_tooltip())
        dev_grid.addWidget(lbl_out, 2, 0)
        self._output_combo = QComboBox()
        self._output_combo.currentTextChanged.connect(lambda: self._on_dev_changed(False))
        dev_grid.addWidget(self._output_combo, 2, 1)

        self._monitor_cb = QCheckBox("监听")
        self._monitor_cb.toggled.connect(self._on_monitor_toggled)
        self._monitor_cb.setToolTip(
            "监听（耳返）：\n"
            "将处理后的声音实时回放到指定设备，\n"
            "让你能听到自己说话的效果。\n"
            "右侧下拉框选择监听用的设备，\n"
            "通常是你的耳机。")
        dev_grid.addWidget(self._monitor_cb, 3, 0)
        self._monitor_combo = QComboBox()
        self._monitor_combo.currentTextChanged.connect(self._handle_monitor_changed)
        dev_grid.addWidget(self._monitor_combo, 3, 1)

        root.addLayout(dev_grid)

        # ── VU 电平表 ──
        self._vu_panel = VUPanel()
        root.addWidget(self._vu_panel)

        # ── 第5行：启动/退出 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.setContentsMargins(0, 0, 0, 0)
        self._start_btn = QPushButton("启动音频处理")
        self._start_btn.setFixedHeight(38)
        self._start_btn.setObjectName("startBtn")
        self._start_btn.clicked.connect(self._on_start_stop)
        self._start_btn.setToolTip(
            "启动/停止音频处理引擎。\n"
            "首次启动会加载 AI 模型（约 1~2 秒），\n"
            "之后即可实时处理麦克风音频。\n"
            "快捷键: 右 Alt + >")
        btn_row.addWidget(self._start_btn)
        self._quit_btn = QPushButton("退出")
        self._quit_btn.setFixedHeight(38)
        self._quit_btn.setObjectName("quitBtn")
        self._quit_btn.clicked.connect(lambda: quit_app(_state.root))
        btn_row.addWidget(self._quit_btn)
        root.addLayout(btn_row)

    def _save(self):
        if self._saver:
            self._saver.request_save()
        else:
            self._config.save_config()

    def _load_config(self):
        api = self._config.get("api_type", default_api_type())
        # 平台感知：配置值可能来自其它平台（如 Windows 配置 WASAPI=13），
        # 若不在当前平台可选 API 中则回退到平台默认。
        if api not in [o[1] for o in self._api_opts]:
            api = default_api_type()
            self._config.set("api_type", api)
        self._api_type = api
        name = next((o[0] for o in self._api_opts if o[1] == api), self._api_opts[0][0])
        self._api_combo.setCurrentText(name)
        self._update_devices()

        self._pre_gain = self._config.get("pre_gain_db", 0.0)

    def _handle_api_changed(self, text):
        try:
            self._api_type = next(o[1] for o in self._api_opts if o[0] == text)
            self._config.set("api_type", self._api_type)
            self._log.dev(f"接口: {text}")
            self._update_api_ui()
            if self._api_type != API_TYPE_NETWORK:
                self._update_devices()
            else:
                port = self._config.get("server_port", 59123)
                lan_ip = get_local_lan_ip()
                url = f"https://{lan_ip}:{port}"
                self._input_url.setText(url)
                self._config.set("NETWORK_input_url", url)
            self._save()
            if self._on_api_changed:
                self._on_api_changed(self._api_type)
        except Exception as e:
            self._log.err(f"接口切换失败: {e}")

    def _on_dev_changed(self, is_input):
        try:
            name = _combo_value(self._input_combo if is_input else self._output_combo)
            self._config.set(self._device_key("input" if is_input else "output"), name)
            self._save()
            # 切换输出设备时，若监听与输出相同则自动关闭
            if (not is_input
                    and self._monitor_cb.isChecked() and self._is_monitor_same_as_output()):
                self._monitor_cb.setChecked(False)
                self._log.dev("监听与输出设备相同，已关闭监听")
            if self._on_device_changed:
                self._on_device_changed(is_input, name)
        except Exception as e:
            self._log.err(f"设备切换失败: {e}")

    def _handle_monitor_changed(self):
        try:
            name = _combo_value(self._monitor_combo)
            self._config.set(self._device_key("monitor"), name)
            self._save()
            if self._monitor_cb.isChecked() and self._get_thread:
                th = self._get_thread()
                if th:
                    if IS_LINUX:
                        th.set_monitor_port(name, True)
                    else:
                        mid = self._get_monitor_id()
                        if mid:
                            th.set_monitor_enabled(True, mid)
        except Exception as e:
            self._log.err(f"监听切换失败: {e}")

    def _is_monitor_same_as_output(self):
        """检查监听设备是否和输出设备相同"""
        m = _combo_value(self._monitor_combo)
        o = _combo_value(self._output_combo)
        return bool(m and o and m == o)

    def _on_monitor_toggled(self, on):
        try:
            if on and not _combo_value(self._monitor_combo):
                self._monitor_cb.setChecked(False)
                self._log.dev("请先选择监听设备")
                return
            if on and self._is_monitor_same_as_output():
                self._monitor_cb.setChecked(False)
                self._log.dev("监听与输出设备相同，已关闭监听")
                return
            self._config.set("monitor_enabled", on)
            self._save()
            if self._get_thread:
                th = self._get_thread()
                if th:
                    if IS_LINUX:
                        th.set_monitor_port(_combo_value(self._monitor_combo), on)
                    else:
                        th.set_monitor_enabled(on, self._get_monitor_id() if on else None)
            if self._monitor_changed_cb:
                self._monitor_changed_cb(on, self._get_monitor_id() if on else None)
        except Exception as e:
            self._log.err(f"监听切换失败: {e}")

    # ── TSE 参考音频弹框 ──

    def _open_ref_dialog(self):
        """打开 TSE 参考音频弹框（录音/播放/文件信息）。"""
        from dialog_tse_reference import TseReferenceDialog
        dlg = TseReferenceDialog(self._config, self._log, parent=self)
        dlg.exec()



    # ── 公共接口 ──

    def set_get_processing_thread_callback(self, cb):
        self._get_thread = cb

    def set_monitor_changed_callback(self, cb):
        self._monitor_changed_cb = cb

    def suspend_ui_timers(self):
        pass  # AGC 轮询已随插件化移除
    def resume_ui_timers(self):
        pass

    def _update_devices(self):
        try:
            is_network = self._api_type == API_TYPE_NETWORK
            if is_network:
                url = self._config.get("NETWORK_input_url", "")
                if not url or "127.0.0.1" in url:
                    port = self._config.get("server_port", 59123)
                    lan_ip = get_local_lan_ip()
                    url = f"https://{lan_ip}:{port}"
                    self._config.set("NETWORK_input_url", url)
                self._input_url.setText(url)
                enum_api = default_api_type()
            else:
                enum_api = self._api_type

            inp, out = get_device_names(api_type=enum_api)
            si = self._config.get(self._device_key("input"))
            so = self._config.get(self._device_key("output"))
            sm = self._config.get(self._device_key("monitor"))

            self._input_combo.blockSignals(True)
            self._output_combo.blockSignals(True)
            self._monitor_combo.blockSignals(True)

            # Linux：PipeWire 用 node.name（显示职责标记），存 userData；
            # 下拉框显示文本、选中取 userData。
            if IS_LINUX:
                from pvplatform.audio.pwpipe_client import source_label, dest_label
                in_ports = [(source_label(p), p) for p in inp] if not is_network else []
                out_ports = [(dest_label(p), p) for p in out]
                self._input_combo.clear()
                for text, data in in_ports:
                    self._input_combo.addItem(text, data)
                self._output_combo.clear()
                for text, data in out_ports:
                    self._output_combo.addItem(text, data)
                self._monitor_combo.clear()
                for text, data in out_ports:
                    self._monitor_combo.addItem(text, data)
            else:
                self._input_combo.clear()
                self._input_combo.addItems(inp if not is_network else [])
                self._output_combo.clear()
                self._output_combo.addItems(out)
                self._monitor_combo.clear()
                self._monitor_combo.addItems(out)

            self._output_combo.setEnabled(True)
            self._output_combo.setToolTip(_output_tooltip())

            def _set_by_port(combo, port):
                """按端口（userData）选中项；找不到返回 False。"""
                idx = combo.findData(port) if port else -1
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                    return True
                return False

            # 输入选择（配置存的是描述名，不匹配时强制回退枚举列表第一个并写回）
            if not is_network:
                if IS_LINUX:
                    if not si or not _set_by_port(self._input_combo, si):
                        _set_by_port(self._input_combo, _jack_default_mic() if inp else "")
                        if self._input_combo.currentIndex() < 0 and inp:
                            self._input_combo.setCurrentIndex(0)
                        if self._input_combo.currentIndex() >= 0:
                            self._config.set(self._device_key("input"),
                                             _combo_value(self._input_combo))
                elif si in inp:
                    self._input_combo.setCurrentText(si)
                elif inp:
                    self._input_combo.setCurrentText(inp[0])
                    self._config.set(self._device_key("input"), inp[0])

            # 输出选择：Linux 一律按 node.name（userData）选
            if IS_LINUX:
                if not so or not _set_by_port(self._output_combo, so):
                    _set_by_port(self._output_combo, _jack_default_sink() if out else "")
                    if self._output_combo.currentIndex() < 0 and out:
                        self._output_combo.setCurrentIndex(0)
                    if self._output_combo.currentIndex() >= 0:
                        self._config.set(self._device_key("output"),
                                         _combo_value(self._output_combo))
            elif so in out:
                self._output_combo.setCurrentText(so)
            elif out:
                self._output_combo.setCurrentText(out[0])
                self._config.set(self._device_key("output"), out[0])

            # 监听设备：Linux 按 node.name（userData）选，其余按文本选
            if sm:
                if not _set_by_port(self._monitor_combo, sm):
                    self._monitor_combo.setCurrentText(sm)

        except Exception as e:
            self._log.err(f"设备枚举失败: {e}")

    def get_device_ids(self):
        try:
            if self._api_type == API_TYPE_NETWORK:
                # 网络模式：无本地输入设备，输出设备用平台默认 API 查找
                return (
                    None,
                    get_device_id(_combo_value(self._output_combo), False, api_type=default_api_type())
                )
            return (
                get_device_id(_combo_value(self._input_combo), True, api_type=self._api_type),
                get_device_id(_combo_value(self._output_combo), False, api_type=self._api_type)
            )
        except Exception as e:
            self._log.err(f"获取设备ID失败: {e}")
            return None, None

    def _get_monitor_id(self):
        try:
            m = _combo_value(self._monitor_combo)
            api = default_api_type() if self._api_type == API_TYPE_NETWORK else self._api_type
            return get_device_id(m, False, api_type=api) if m else None
        except:
            return None

    def get_api_type(self):
        return self._api_type

    def get_pre_gain(self):
        return self._pre_gain


    def _get_root(self):
        return self.window()

    def _device_key(self, kind: str) -> str:
        """当前接口对应的设备配置键（kind: input/output/monitor/far）。

        设备名按接口隔离存储（如 input_device_wasapi / input_device_mme），
        因为 WASAPI 与 MME（以及 Linux 各接口）的设备名完全不一致。
        monitor（监听设备）与 far（AEC 回声参考 sink）是不同概念，各自独立
        存键：monitor_device_<接口> 与 aec_far_sink_<接口>。
        网络模式没有本地输入，输出/监听/AEC far 用平台默认接口的键。
        """
        api = self._api_type
        if api == API_TYPE_NETWORK:
            api = default_api_type()
        if kind == "far":
            return f"aec_far_sink_{device_config_suffix(api)}"
        return f"{kind}_device_{device_config_suffix(api)}"

    def _on_url_changed(self):
        self._save_url()

    def _save_url(self):
        url = self._input_url.text().strip()
        self._config.set("NETWORK_input_url", url)
        import re
        m = re.search(r":(\d+)", url)
        if m:
            port = int(m.group(1))
            self._config.set("server_port", port)
        self._save()
        if self._log:
            self._log.dev(f"API网址: {url}")

    def _update_api_ui(self):
        is_network = self._api_type == API_TYPE_NETWORK
        self._lbl_in.setText("API网址" if is_network else "输入设备")
        self._lbl_in.setToolTip(
            "API网址 — 手机/网页推流到本机的地址\n格式: https://本机IP:端口\n例如: https://192.168.1.100:59123"
            if is_network else
            ("输入设备 — PipeWire 音频源节点（48kHz 单声道）\n"
             "例如 alsa_input.pci-…Mic1__source，\n"
             "选中后 PureVox 输入流自动接管该节点。"
             if IS_LINUX else
             "输入设备 — 你的物理麦克风\n选择你正在使用的麦克风，\n例如 Realtek Audio、USB 麦克风等。")
        )
        self._input_combo.setVisible(not is_network)
        self._input_url.setVisible(is_network)

    def refresh_devices(self):
        self._update_devices()

    def _on_start_stop(self):
        toggle_processing(_state, self._log)

    def update_start_button(self, running):
        from theme_colors import current_colors
        tc = current_colors()
        if running:
            self._start_btn.setText("停止音频处理")
            self._start_btn.setStyleSheet(
                f"#startBtn {{ background-color: {tc.stop_btn_bg}; color: white; border-radius: 4px; font-size: 11pt; font-weight: bold; padding: 4px 16px; }}"
                f"#startBtn:hover {{ background-color: {tc.stop_btn_hover}; }}")
        else:
            self._start_btn.setText("启动音频处理")
            self._start_btn.setStyleSheet(
                f"#startBtn {{ background-color: {tc.start_btn_bg}; color: white; border-radius: 4px; font-size: 11pt; font-weight: bold; padding: 4px 16px; }}"
                f"#startBtn:hover {{ background-color: {tc.start_btn_hover}; }}")


class MainWindow(QMainWindow):
    WM_SETTINGCHANGE = 0x001A
    WM_POWERBROADCAST = 0x0218
    PBT_APMSUSPEND = 0x0004
    PBT_APMRESUMEAUTOMATIC = 0x0012
    PBT_APMRESUMESUSPEND = 0x0007

    def __init__(self, config, logger):
        super().__init__()
        self.setWindowTitle(f"PureVox {BUILD_DATE}")
        # 禁止拖动调整大小
        self.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint | Qt.WindowCloseButtonHint | Qt.MSWindowsFixedSizeDialogHint)

        # ── 守护定时器 ──
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(3000)
        self._watchdog_timer.timeout.connect(self._watchdog_check)
        self._watchdog_timer.start()

        # 去掉 QMainWindow 内部布局的多余间距
        if self.layout():
            self.layout().setSpacing(0)
            self.layout().setContentsMargins(0, 0, 0, 0)

        central = QWidget()
        self.setCentralWidget(central)
        self._layout = QVBoxLayout(central)
        self._layout.setSpacing(0)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def add_widget(self, widget, stretch=0):
        self._layout.addWidget(widget, stretch)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def changeEvent(self, event):
        """窗口最小化→暂停 VU/频谱，恢复→开启"""
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                _suspend_ui_timers(_state)
            else:
                _resume_ui_timers(_state)

    def hideEvent(self, event):
        """窗口最小化到托盘 — 暂停 UI 定时器省资源"""
        super().hideEvent(event)
        _suspend_ui_timers(_state)

    def showEvent(self, event):
        """窗口从托盘恢复 — 恢复 UI 定时器"""
        super().showEvent(event)
        _resume_ui_timers(_state)

    def nativeEvent(self, eventType, message):
        # 仅 Windows 有原生消息（主题变更 / 电源事件）；其它平台直接透传
        if IS_WINDOWS and eventType == b"windows_generic_MSG":
            try:
                import ctypes.wintypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_SETTINGCHANGE:
                    QTimer.singleShot(200, self._on_theme_changed)
                elif msg.message == self.WM_POWERBROADCAST:
                    self._on_power_event(msg.wParam)
            except:
                pass
        return super().nativeEvent(eventType, message)

    def _on_power_event(self, wparam):
        """系统电源事件：睡眠前停止处理，唤醒后自动恢复。"""
        if wparam == self.PBT_APMSUSPEND:
            _state.was_processing_before_sleep = _state.is_processing
            if _state.is_processing:
                stop_processing(_state, _state.logger or get_logger())
        elif wparam in (self.PBT_APMRESUMEAUTOMATIC, self.PBT_APMRESUMESUSPEND):
            if _state.was_processing_before_sleep:
                _state.was_processing_before_sleep = False
                # 设备需要时间重新枚举（USB 设备尤其）
                QTimer.singleShot(3000, self._wake_restart_step1)

    def _wake_restart_step1(self):
        """唤醒恢复第1步：刷新设备列表。"""
        mp = _state.main_panel
        if mp:
            mp.refresh_devices()
        QTimer.singleShot(2000, self._wake_restart_step2)

    def _wake_restart_step2(self):
        """唤醒恢复第2步：尝试启动处理。"""
        if _state.is_processing:
            return
        start_processing(_state, _state.logger or get_logger())
        if not _state.is_processing:
            # 失败则 5 秒后再试一次
            QTimer.singleShot(5000, lambda: start_processing(
                _state, _state.logger or get_logger()))

    def _watchdog_check(self):
        """守护检查：线程意外死亡时自动重启。"""
        if not _state.is_processing:
            return
        th = _state.processing_thread
        if th is None:
            return
        try:
            if hasattr(th, 'is_alive') and not th.is_alive():
                _state.logger.sys("[守护] 音频线程意外退出，尝试自动恢复...") if _state.logger else None
                stop_processing(_state, _state.logger or get_logger())
                QTimer.singleShot(500, self._restart_after_crash)
        except Exception:
            pass

    def _restart_after_crash(self):
        """守护触发的恢复：刷新设备后启动（失败不重试，避免弹框循环）"""
        mp = _state.main_panel
        if mp:
            mp.refresh_devices()
        start_processing(_state, _state.logger or get_logger())

    def _on_theme_changed(self):
        """系统主题变化 → 统一同步入口。"""
        self.setUpdatesEnabled(False)
        _sync_theme_ui(QApplication.instance(), _state.config)
        self.setUpdatesEnabled(True)
        self.repaint()


# ═══════════════════════════════════════════════════════════════
#  全局函数
# ═══════════════════════════════════════════════════════════════


def _warn_48k(failed_in, failed_out, failed_mon, failed_aec,
              in_name, out_name, mon_name, aec_name, log):
    """设备非 48k 弹框：纯文字，区分播放/录制选项卡，设备名加边框"""
    global _LAST_48K_WARN
    import time as _time
    now = _time.time()
    if now - _LAST_48K_WARN < 2.0:
        return
    _LAST_48K_WARN = now

    dlg = QDialog(None)
    dlg.setWindowTitle("PureVox")
    dlg.setMinimumWidth(380)
    dlg.setWindowModality(Qt.ApplicationModal)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)
    layout.setContentsMargins(16, 16, 16, 12)

    title = QLabel("以下设备不支持 48kHz，无法启动：")
    title.setStyleSheet("font-size: 11pt;")
    layout.addWidget(title)

    # 设备列表，区分播放/录制，加边框
    dev_style = (
        "QFrame { border: 1px solid palette(mid); border-radius: 4px; "
        "background: palette(base); padding: 4px 6px; }"
    )
    dev_layout = QVBoxLayout()
    dev_layout.setSpacing(4)
    if failed_in:
        row = QHBoxLayout()
        tag = QLabel(" 录制 ")
        tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
        row.addWidget(tag)
        row.addWidget(QLabel(in_name))
        row.addStretch()
        dev_layout.addLayout(row)
    if failed_out:
        row = QHBoxLayout()
        tag = QLabel(" 播放 ")
        tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
        row.addWidget(tag)
        row.addWidget(QLabel(out_name))
        row.addStretch()
        dev_layout.addLayout(row)
    if failed_mon:
        row = QHBoxLayout()
        tag = QLabel(" 监听 ")
        tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
        row.addWidget(tag)
        row.addWidget(QLabel(mon_name))
        row.addStretch()
        dev_layout.addLayout(row)
    if failed_aec:
        row = QHBoxLayout()
        tag = QLabel(" AEC播放 ")
        tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
        row.addWidget(tag)
        row.addWidget(QLabel(aec_name))
        row.addStretch()
        dev_layout.addLayout(row)

    dev_frame = QFrame()
    dev_frame.setStyleSheet(dev_style)
    dev_frame.setLayout(dev_layout)
    layout.addWidget(dev_frame)

    hint = QLabel("请将设备采样率设为 48kHz 后重试。")
    hint.setStyleSheet("font-size: 10pt;")
    layout.addWidget(hint)

    # 按钮行
    btn_row = QHBoxLayout()
    if failed_out or failed_mon or failed_aec:
        btn_out = QPushButton("打开播放选项卡")
        btn_out.clicked.connect(lambda: open_sound_panel(log) or dlg.accept())
        btn_out.setFixedHeight(28)
        btn_row.addWidget(btn_out)
    if failed_in:
        btn_in = QPushButton("打开录制选项卡")
        if sys.platform.startswith("linux"):
            btn_in.clicked.connect(lambda: open_sound_panel(log) or dlg.accept())
        else:
            btn_in.clicked.connect(lambda: os.system("control mmsys.cpl,,1") or dlg.accept())
        btn_in.setFixedHeight(28)
        btn_row.addWidget(btn_in)
    btn_row.addStretch()
    btn_ok = QPushButton("确定")
    btn_ok.setFixedHeight(28)
    btn_ok.setFixedWidth(72)
    btn_ok.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_ok)
    layout.addLayout(btn_row)

    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    # 用 exec() 运行模态事件循环，避免手动 processEvents 循环在设备切换
    # 触发 restart 时嵌套重入导致 UI 卡死。
    dlg.exec()


def start_processing(state, log):
    if state.is_processing:
        return
    # 防重入：防止事件循环中重复触发
    if getattr(start_processing, '_lock', False):
        return
    start_processing._lock = True
    try:
        mp = state.main_panel
        if state.processor:
            try:
                if hasattr(state.processor, 'cleanup'):
                    state.processor.cleanup()
            except Exception:
                pass
            state.processor = None

        # ── 插件链：全部处理行为由此决定 ──
        chain_cfg = [dict(e) for e in (state.config.get("plugin_chain", []) if state.config else [])]

        def _chain_enabled(ptype):
            return any(e.get("type") == ptype and e.get("enabled", True)
                       for e in chain_cfg)

        def _chain_param(ptype, key, default=""):
            for e in chain_cfg:
                if e.get("type") == ptype and e.get("enabled", True):
                    return (e.get("params") or {}).get(key, default)
            return default

        # ── 设备检测 ──
        inp, out, api_type = _get_ids(state)
        is_network = api_type == API_TYPE_NETWORK

        # Linux：输入/输出/监听是原生 PipeWire node.name，
        # 下拉框选中即接管。PortAudio 不参与本地输入/输出。
        pw_ports = ("", "", "")
        use_pw = IS_LINUX and not is_network
        if use_pw:
            mp_in = _combo_value(state.main_panel._input_combo) if hasattr(state.main_panel, '_input_combo') else ""
            mp_out = _combo_value(state.main_panel._output_combo) if hasattr(state.main_panel, '_output_combo') else ""
            mp_mon = _combo_value(state.main_panel._monitor_combo) if hasattr(state.main_panel, '_monitor_combo') else ""
            mon_on = (state.main_panel._monitor_cb.isChecked()
                      if hasattr(state.main_panel, '_monitor_cb') else False)
            pw_ports = (mp_in, mp_out, mp_mon if mon_on else "")
            if not mp_in or not mp_out:
                log.err("请选择 PipeWire 输入与输出节点")
                return
            inp = None
            out = None
            log.msg(f"[启动] PipeWire 输入: {mp_in}\n[启动] PipeWire 输出: {mp_out}"
                    + (f"\n[启动] PipeWire 监听: {mp_mon}" if mp_mon else ""))

        def _try_open_48k(_p, device_id, is_input):
            """尝试以 48kHz 打开设备流（共用 PyAudio 实例），成功返回 True。
            失败时记录诊断日志（设备名/默认采样率/通道数/宿主 API/异常原因），
            区分「真不支持 48k」与「设备被占用/独占」等资源性问题。"""
            if device_id is None:
                return True
            try:
                if is_input:
                    s = _p.open(format=pyaudio.paFloat32, channels=1,
                                rate=48000, input=True,
                                input_device_index=device_id,
                                frames_per_buffer=1024)
                else:
                    s = _p.open(format=pyaudio.paFloat32, channels=1,
                                rate=48000, output=True,
                                output_device_index=device_id,
                                frames_per_buffer=1024)
                s.close()
                return True
            except Exception as e:
                try:
                    from pvplatform.audio import device_api as _dapi
                    try:
                        info = _p.get_device_info_by_index(device_id)
                        info = dict(info)
                    except Exception:
                        info = {}
                    name = _dapi.fix_device_name(info.get('name', '?'))
                    try:
                        host = _p.get_host_api_info(info['hostApi'])['name'] if info.get('hostApi') is not None else "?"
                    except Exception:
                        host = "?"
                    detail = (
                        f"device={device_id} name={name!r} "
                        f"sr={info.get('defaultSampleRate', '?')} "
                        f"in_ch={info.get('maxInputChannels', '?')} "
                        f"out_ch={info.get('maxOutputChannels', '?')} "
                        f"host={host}"
                    )
                except Exception:
                    detail = f"device={device_id}（设备信息读取失败）"
                log.warn(f"[48k检测] {('输入' if is_input else '输出')}打开失败: "
                         f"{detail} 原因: {e}")
                return False

        # 检测输入/输出/监听设备（共用 PyAudio 避免频繁创建）。
        # PipeWire 模式跳过 PortAudio 检测：格式协商已固定 48kHz 单声道，
        # 重采样/声道转换由 PipeWire 处理，_create_stream 会校验协商采样率。
        failed_in = False   # 输入（录制）设备
        failed_out = False  # 输出（播放）设备
        failed_mon = False
        failed_aec = False
        in_name = out_name = mon_name = aec_name = ""
        if not is_network and not use_pw:
            _p = pyaudio.PyAudio()
            try:
                in_name = mp._input_combo.currentText() if mp else ""
                out_name = mp._output_combo.currentText() if mp else ""
                mon_id = mp._get_monitor_id() if mp else None
                mon_checked = mp._monitor_cb.isChecked() if mp else False

                if not _try_open_48k(_p, inp, True):
                    failed_in = True
                if not _try_open_48k(_p, out, False):
                    failed_out = True
                if mon_checked and mon_id is not None:
                    mon_name = mp._monitor_combo.currentText() if mp else ""
                    if not _try_open_48k(_p, mon_id, False):
                        failed_mon = True
                # AEC speaker 检测：启用回声消除时检测 far 参考输出（默认播放设备）
                if _chain_enabled("echo_cancel"):
                    try:
                        aec_dev = _p.get_default_output_device_info()
                        aec_id = aec_dev['index']
                        aec_name = aec_dev['name']
                        if not _try_open_48k(_p, aec_id, False):
                            failed_aec = True
                    except Exception:
                        pass
                log.msg(
                    "[48k检测] 输入=%s 输出=%s 监听=%s AEC=%s"
                    % ("OK" if not failed_in else "FAIL",
                       "OK" if not failed_out else "FAIL",
                       "OK" if not failed_mon else "FAIL",
                       "OK" if not failed_aec else ("FAIL" if _chain_enabled("echo_cancel") else "跳过")))
            finally:
                _p.terminate()

        if failed_in or failed_out or failed_mon or failed_aec:
            _names = [n for n in (in_name, out_name, mon_name, aec_name) if n]
            log.err("[48k检测] 存在不支持 48kHz 的设备，已弹框阻止启动: %s"
                    % ("、".join(_names) if _names else "（设备名读取失败）"))
            _warn_48k(failed_in, failed_out, failed_mon, failed_aec,
                      in_name, out_name, mon_name, aec_name, log)
            return

        pre = mp.get_pre_gain() if mp else 0

        # ── 创建处理器（模型由插件内部按需解析加载）──
        log.msg(f"[启动] 创建音频处理器 (pre={pre})...")
        proc = create_audio_processor(pre, "", "", "")
        state.processor = proc
        if proc.plugin_errors:
            for perr in proc.plugin_errors:
                log.warn(f"[插件] {perr}")

        # ── EQ（垫片路由到链中的 eq 插件）──
        if state.config:
            gains = state.config.get("eq_current_gains", [0.0] * 61)
            if gains:
                proc.set_eq_gains(gains)

        # ── 加载插件链 ──
        try:
            proc.set_plugins(chain_cfg)
        except Exception as e:
            log.warn(f"[启动] 插件链加载失败: {e}")

        # ── 监听 ──
        # PipeWire 模式：监听是独立的输出流（已在 pw_ports[2]）；
        # 其余平台用 PortAudio 监听流。
        mid = None
        if not use_pw and mp and state.config and state.config.get("monitor_enabled", False):
            mid = mp._get_monitor_id()

        # ── TSE 参考音频：必须在启动音频线程前加载（首帧就需要）──
        # 无参考时不中止：TSE 插件自动直通，录完参考后下次启动/重启生效。
        tse_pending_ref = False
        if _chain_enabled("tse"):
            wav = state.config.get(CFG_REF_WAV_PATH, "") if state.config else ""
            if wav and os.path.exists(wav) and load_tse_reference(proc, wav):
                pass  # 参考就绪
            else:
                tse_pending_ref = True
                log.warn("TSE 暂无参考音频：该插件将直通。可在右侧「目标说话人 TSE」行点「参考音频…」录制。")

        # ── 启动日志 ──
        active = [e.get("type") for e in chain_cfg if e.get("enabled", True)]
        parts = ["+".join(active) if active else "空链"]
        if tse_pending_ref:
            parts.append("TSE 待录参考")
        if is_network:
            parts.append("网络输入")
        ready_msg = " · ".join(parts)

        # ── 启动音频流 ──
        if is_network:
            if mp and hasattr(mp, '_save_url'):
                mp._save_url()
            server = _ensure_network_server(state, log)
            # Linux 网络模式：输出同样走 PipeWire（输出下拉框即目标节点）
            net_pw = ("", "", "")
            if IS_LINUX and mp:
                net_pw = ("", _combo_value(mp._output_combo) if hasattr(mp, '_output_combo') else "", "")
            state.processing_thread = start_audio_stream(
                None, None if IS_LINUX else out, proc, HOP_LENGTH, mid,
                network_source=server.audio_source,
                api_type=api_type,
                ready_msg=ready_msg,
                pw_ports=net_pw if IS_LINUX else ())
        else:
            api_name = get_api_name_by_type(api_type)
            in_dev = mp._input_combo.currentText() if mp else ""
            out_dev = mp._output_combo.currentText() if mp else ""
            if use_pw:
                log.msg(f"[启动] PipeWire: {in_dev} → PureVox → {out_dev}（48kHz 单声道）")
            else:
                log.msg(f"[启动] {api_name} 输入#{inp} ({in_dev}) → 输出#{out} ({out_dev})")
            state.processing_thread = start_audio_stream(
                inp, out, proc, HOP_LENGTH, mid,
                api_type=api_type, ready_msg=ready_msg,
                pw_ports=pw_ports if use_pw else ())

        # 等待音频流创建结果（_create_stream 在子线程异步执行）
        if state.processing_thread:
            import time as _t
            if not state.processing_thread.wait_ready(timeout=3.0):
                err = getattr(state.processing_thread, '_start_error', None) or "音频流创建超时"
                log.err(f"[启动] 音频流创建失败: {err}")
                try:
                    state.processing_thread.stop()
                except Exception:
                    pass
                state.processing_thread = None
                state.processor = None
                return

        state.is_processing = True
        _update_ui(state, True, log)

        # ── 后续：回声消除扬声器采集（链中启用了 echo_cancel 时）──
        if _chain_enabled("echo_cancel") and state.processing_thread:
            fac_sink = _chain_param("echo_cancel", "far_device", "")
            if not fac_sink and state.config and mp:
                fac_sink = state.config.get(mp._device_key("far"), "") or ""
            state.processing_thread.set_aec_far_sink(fac_sink)
            state.processing_thread.processor.set_aec_enabled(True)
            if not state.processing_thread.set_aec_enabled(True):
                QMessageBox.warning(None, "PureVox",
                    "回声消除扬声器采集启动失败。\n\n请确认所选参考输出设备可用。")

        register_tse_audio_hook(state.processing_thread, log)

    except Exception as e:
        import traceback as _tb
        log.err(f"启动失败: {e}")
        log.err(_tb.format_exc())
    finally:
        start_processing._lock = False


def _run_server_loop(server, loop, log):
    """后台线程：运行 PureVoxServer 的 asyncio 事件循环"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.start())
        loop.run_forever()
    except Exception as e:
        log.err(f"[服务器] 事件循环异常: {e}")
    finally:
        for task in asyncio.all_tasks(loop):
            task.cancel()
        if not loop.is_closed():
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()


def _stop_network_server(state, log):
    """停止网络服务器（从网络模式切换走或退出时调用）。"""
    if state.network_server and state._server_loop:
        try:
            async def _stop():
                await state.network_server.stop()
            fut = asyncio.run_coroutine_threadsafe(_stop(), state._server_loop)
            fut.result(timeout=5)
            state._server_loop.call_soon_threadsafe(state._server_loop.stop)
        except Exception as e:
            log.err(f"[服务器] 停止异常: {e}")
        state.network_server = None
        state._server_loop = None


def _ensure_firewall_rule(exe_path: str, port: int):
    """添加 Windows 防火墙入站规则，以当前 exe 名义开放指定端口。"""
    rule_name = f"PureVox - Remote Mic (端口 {port})"
    try:
        # 先检查是否已存在同名规则
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, text=True, timeout=5
        )
        if "No rules match" not in check.stdout and "没有规则与" not in check.stdout:
            return  # 规则已存在

        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}",
             "dir=in", "action=allow",
             f"program={exe_path}",
             "protocol=tcp",
             f"localport={port}",
             "profile=any"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass  # 防火墙规则非致命，静默失败


def _ensure_network_server(state, log):
    """启动网络服务器（若尚未运行）。返回服务器实例。"""
    if state.network_server is not None and state._server_loop is not None:
        return state.network_server  # already running

    port = state.config.get("server_port", 59123) if state.config else 59123
    _ensure_firewall_rule(sys.executable, port)
    server = PureVoxServer(port=port)
    server.set_logger(log.msg)
    state.network_server = server
    loop = asyncio.new_event_loop()
    state._server_loop = loop
    threading.Thread(target=_run_server_loop, args=(server, loop, log), daemon=True).start()
    log.msg(f"[服务器] 已启动 (端口 {port})")
    return server


def stop_processing(state, log):
    if not state.is_processing:
        return
    log.msg("[停止] 停止音频处理... (关闭流 + 清理处理器)")
    try:
        # 停止 AGC 轮询定时器
        mp = state.main_panel
        if mp and hasattr(mp, '_agc_poll_timer'):
            mp._agc_poll_timer.stop()

        # 注意：网络服务器保持运行 — 不在此处停止，避免手机断开。
        # 服务器仅在切换 API 类型离开网络模式时、或退出应用时停止。

        if state.processing_thread:
            state.processing_thread.stop()
            state.processing_thread = None

        if state.processor:
            try:
                if hasattr(state.processor, 'cleanup'):
                    state.processor.cleanup()
            except Exception as e:
                log.err(f"清理处理器: {e}")
            finally:
                state.processor = None

        state.is_processing = False
        _update_ui(state, False, log)
        log.msg("[停止] 音频处理已停止")

        if state.main_panel:
            state.main_panel.refresh_devices()
    except Exception as e:
        import traceback as _tb
        log.err(f"停止失败: {e}")
        log.err(_tb.format_exc())


def _get_ids(state):
    if not state.main_panel:
        return None, None, None
    mp = state.main_panel
    api_type = mp.get_api_type()
    if api_type == API_TYPE_NETWORK:
        url = state.config.get("NETWORK_input_url", "") if state.config else ""
        inp_id = ("NETWORK", url)
        out_id = get_device_id(_combo_value(mp._output_combo), False, api_type=default_api_type())
    else:
        inp_id, out_id = mp.get_device_ids()
    return inp_id, out_id, api_type


def _update_ui(state, running, log):
    if state.main_panel:
        state.main_panel.update_start_button(running)
    icon_path = state.icon_on_path if running else state.icon_off_path
    if icon_path and os.path.exists(icon_path):
        icon = QIcon(icon_path)
        if state.root:
            state.root.setWindowIcon(icon)
        if state.tray_icon:
            state.tray_icon.setIcon(icon)
    if state.tray_icon:
        state.tray_icon.setToolTip(f"PureVox - {'运行中' if running else '未运行'}")

    # VU 电平表数据更新定时器
    if running:
        if not hasattr(state, '_viz_timer'):
            state._viz_timer = QTimer()
            state._viz_timer.setInterval(16)  # ~60fps
            state._viz_timer.timeout.connect(lambda: _feed_visualizer(state))
        state._viz_timer.start()
    else:
        if hasattr(state, '_viz_timer'):
            state._viz_timer.stop()


def _feed_visualizer(state):
    """定时从处理线程获取音频数据并送入 VU 电平表和频谱图"""
    if not state.processing_thread or not state.is_processing:
        return
    import traceback as _tb
    try:
        th = state.processing_thread
        # VU 电平表（峰值快照）
        if hasattr(state, 'vu_meter') and state.vu_meter:
            peak = getattr(th, '_vu_peak', 0.0)
            if peak > 0:
                state.vu_meter.update_level_db(20.0 * _math.log10(max(peak, 1e-10)))
        # 频谱直方图（只在可见时更新，防止后台浪费资源）
        if hasattr(state, 'spectrum_widget') and state.spectrum_widget and state.spectrum_widget.isVisible():
            in_buf = getattr(th, '_spectrum_in', None)
            out_buf = getattr(th, '_spectrum_out', None)
            in_data = in_buf.read_latest(min(2048, in_buf.available())) if in_buf and hasattr(in_buf, 'read_latest') and in_buf.available() > 0 else None
            out_data = out_buf.read_latest(min(2048, out_buf.available())) if out_buf and hasattr(out_buf, 'read_latest') and out_buf.available() > 0 else None
            # 丢弃空列表（防御性检查）
            if isinstance(in_data, list) and len(in_data) == 0:
                in_data = None
            if isinstance(out_data, list) and len(out_data) == 0:
                out_data = None
            if in_data or out_data:
                state.spectrum_widget.update_spectrum(in_data, out_data)
    except Exception:
        _log = state.logger or get_logger()
        _log.err(f"[频谱] _feed_visualizer 异常: {_tb.format_exc()}")


def _suspend_ui_timers(state):
    """窗口隐藏到托盘时暂停 UI 刷新定时器（省 CPU）"""
    if hasattr(state, '_viz_timer'):
        state._viz_timer.stop()
    if state.main_panel and hasattr(state.main_panel, 'suspend_ui_timers'):
        state.main_panel.suspend_ui_timers()
    # 暂停 VU/频谱数据采集
    if state.processing_thread:
        state.processing_thread.set_viz_enabled(False)


def _resume_ui_timers(state):
    """窗口从托盘恢复时重启 UI 刷新定时器"""
    if hasattr(state, '_viz_timer') and state.is_processing:
        state._viz_timer.start()
    if state.main_panel and hasattr(state.main_panel, 'resume_ui_timers'):
        state.main_panel.resume_ui_timers()
    # 恢复 VU/频谱数据采集
    if state.processing_thread:
        state.processing_thread.set_viz_enabled(True)


def toggle_processing(state, log):
    if state.is_processing:
        stop_processing(state, log)
        _sys_beep(440, 160)
    else:
        start_processing(state, log)
        if state.is_processing:
            _sys_beep(880, 140)


def _virtual_mic_dialog(logger, window=None):
    """虚拟声卡面板：Linux 用 dialog_virtual_mic_linux 手动创建/清理；
    Windows 为 VB-CABLE 检测弹框。"""
    if IS_LINUX:
        from dialog_virtual_mic_linux import show_virtual_mic_dialog
        refresh = (lambda: _state.main_panel.refresh_devices()
                   if _state.main_panel else None)
        api_type = _state.main_panel.get_api_type() if _state.main_panel else 0
        show_virtual_mic_dialog(logger, refresh_devices=refresh, api_type=api_type)
    elif IS_WINDOWS:
        from dialog_vbcable_check import show_vbcable_dialog
        show_vbcable_dialog(_state.config)


def quit_app(window):
    log = _state.logger or get_logger()
    _stop_network_server(_state, log)
    stop_processing(_state, log)
    if _state.tray_icon:
        _state.tray_icon.hide()
    # Linux 采用「检测-重置」模型：退出不卸载虚拟麦克风（避免频繁创建/删除），
    # 下次启动 detect 到已存在便不重建；异常时用菜单「重置虚拟音频」手动处理。
    QApplication.quit()


class MainApp:
    def __init__(self):
        self._window = None

    def _init_config(self):
        from user_paths import CONFIG_PATH, ensure_dirs
        ensure_dirs()
        config = ConfigManager(CONFIG_PATH)
        config.set("registry_auto_start", self._is_boot())
        config.save_config()
        return config

    def _is_boot(self):
        return is_autostart()

    def _create_menu(self, window, config, logger):
        # ── 原生菜单栏 ──
        from PySide6.QtWidgets import QMenu
        menubar = window.menuBar()

        # 设置菜单（下拉）
        settings_menu = menubar.addMenu("设置")
        hk = QAction("快捷键 (右Alt+>)", window)
        hk.setCheckable(True)
        hk.setChecked(config.get("hotkey_enabled", True))
        hk.triggered.connect(lambda: toggle_hotkey(config, logger))
        settings_menu.addAction(hk)
        auto = QAction("启动时自动运行", window)
        auto.setCheckable(True)
        auto.setChecked(config.get("auto_start", False))
        auto.triggered.connect(lambda: toggle_auto_start(config, logger))
        settings_menu.addAction(auto)
        boot = QAction("开机自启", window)
        boot.setCheckable(True)
        boot.setChecked(config.get("registry_auto_start", False))
        boot.triggered.connect(lambda: self._toggle_boot(logger))
        settings_menu.addAction(boot)

        # 主题放在设置里
        theme_menu = settings_menu.addMenu("主题")
        theme_labels = {"system": "系统", "light": "白天", "dark": "黑夜"}
        theme_values = ["system", "light", "dark"]
        current_theme = config.get("theme", "system")
        self._theme_menu = theme_menu
        self._theme_labels = theme_labels

        for tv in theme_values:
            a = QAction(theme_labels[tv], window)
            a.setCheckable(True)
            a.setChecked(tv == current_theme)
            a.triggered.connect(lambda *x, _tv=tv: self._set_theme(
                config, logger, _tv))
            theme_menu.addAction(a)

        # 顶层快捷操作
        snd = QAction("系统声音", window)
        snd.triggered.connect(lambda: open_sound_panel(logger))
        menubar.addAction(snd)
        vmic = QAction("虚拟声卡", window)
        vmic.triggered.connect(lambda: _virtual_mic_dialog(logger, window))
        menubar.addAction(vmic)
        about = QAction("关于", window)
        about.triggered.connect(lambda: self._show_about(window))
        menubar.addAction(about)

    def _apply_style(self):
        app = QApplication.instance()
        app.setFont(QFont("Microsoft YaHei", 10))
        app.setStyleSheet("""
            QMenuBar {
                background: palette(window);
                color: palette(window-text);
                font-size: 10pt;
            }
            QMenuBar::item {
                padding: 4px 14px;
                background: transparent;
                color: palette(window-text);
            }
            QMenuBar::item:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            QMenu {
                font-size: 10pt;
                padding: 4px;
            }
            QMenu::item {
                padding: 4px 20px;
                min-height: 18px;
            }
            QMenu::item:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
                border-radius: 4px;
            }
            QMenu::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid palette(mid);
                border-radius: 3px;
            }
            QMenu::indicator:checked {
                background: palette(highlight);
                border-color: palette(highlight);
            }
            QMenu::indicator:unchecked {
                background: transparent;
            }
            QComboBox {
                font-size: 10pt;
                padding: 2px 6px;
                border: 1px solid palette(mid);
                border-radius: 3px;
                background: palette(base);
                color: palette(text);
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background: palette(base);
                color: palette(text);
                selection-background-color: palette(highlight);
                selection-color: palette(highlighted-text);
            }
            QLabel {
                font-size: 10pt;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: palette(mid);
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0;
                background: palette(highlight);
                border-radius: 7px;
            }
            QPushButton {
                min-height: 18px;
                padding: 1px 8px;
                font-size: 10pt;
            }
            QCheckBox {
                color: palette(text);
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid palette(mid);
                border-radius: 3px;
            }
            QCheckBox::indicator:unchecked {
                background: transparent;
                border: 1px solid palette(mid);
            }
            QCheckBox::indicator:checked {
                background: palette(highlight);
                border-color: palette(highlight);
            }
            QCheckBox::indicator:hover {
                border: 1px solid palette(mid);
                background: transparent;
            }
            QCheckBox::indicator:hover:checked {
                background: palette(highlight);
                border-color: palette(highlight);
            }
            QCheckBox::indicator:pressed {
                border: 1px solid palette(mid);
                background: transparent;
            }
            QCheckBox::indicator:pressed:checked {
                background: palette(highlight);
                border-color: palette(highlight);
            }
            QTabWidget::pane {
                border: 1px solid palette(mid);
                border-radius: 4px;
            }
            QTabBar::tab {
                padding: 6px 16px;
                border: 1px solid palette(mid);
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                background: palette(button);
                color: palette(button-text);
            }
            QTabBar::tab:selected {
                background: palette(base);
                color: palette(text);
            }
            QTabBar::tab:hover:!selected {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            #startBtn {
                background-color: #4caf50;
                color: white;
                border-radius: 4px;
                font-size: 11pt;
                font-weight: bold;
                padding: 4px 16px;
            }
            #startBtn:hover {
                background-color: #388e3c;
            }
            #quitBtn {
                background-color: #f44336;
                color: white;
                border-radius: 4px;
                font-size: 11pt;
                font-weight: bold;
                padding: 4px 16px;
            }
            #quitBtn:hover {
                background-color: #d32f2f;
            }
        """)

    def _toggle_boot(self, logger):
        new = not _state.config.get("registry_auto_start", False)
        if new:
            if enable_autostart(logger):
                _state.config.set("registry_auto_start", True)
                _state.config.save_config()
                logger.sys("开机自启: 开")
        else:
            if disable_autostart(logger):
                _state.config.set("registry_auto_start", False)
                _state.config.save_config()
                logger.sys("开机自启: 关")

    def _set_theme(self, config, logger, theme_value):
        """直接设置主题（三选一）。"""
        config.set("theme", theme_value)
        config.save_config()
        logger.sys(f"主题: {self._theme_labels.get(theme_value, theme_value)}")
        # 更新勾选状态
        for action in self._theme_menu.actions():
            action.setChecked(action.text() == self._theme_labels.get(theme_value))
        # 统一同步入口
        _sync_theme_ui(QApplication.instance(), config)

    def _show_about(self, window):
        from dialog_about import show_about_dialog
        show_about_dialog(window)

    def _create_ui(self, window, config, saver, logger):
        def on_api(api):
            # 进入网络模式时立即启动服务器（不等点击"启动处理"）
            if api == API_TYPE_NETWORK:
                _ensure_network_server(_state, logger)
            else:
                _stop_network_server(_state, logger)
            stop_processing_for_update(_state, logger)

        def on_dev(is_input, name):
            if _state.processing_thread and _state.is_processing:
                # 延迟重启：先让 UI 响应设备切换，再后台重启处理，
                # 避免主线程同步 stop+start 阻塞（设备枚举/打开可能耗时）
                QTimer.singleShot(150, lambda: restart(_state, logger))

        mp = MainPanel(window, config, saver, on_api, on_dev, logger)

        from spectrum_histogram import SpectrumWidget

        spectrum = SpectrumWidget()
        _state.spectrum_widget = spectrum

        # 固定尺寸常量
        _state._panel_w = 320   # 左列宽度（设备/控制/VU/频谱）
        _state._fx_w = 380      # 右列宽度（插件面板）
        _state._spectrum_h = 150

        # ── 左列：控制面板 + 频谱（上下堆叠）──
        left_col = QWidget()
        left_lay = QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)
        mp.setFixedWidth(_state._panel_w)
        left_lay.addWidget(mp, 0)
        spectrum.setFixedWidth(_state._panel_w)
        spectrum.setMinimumHeight(_state._spectrum_h)
        left_lay.addWidget(spectrum, 1)

        # ── 右列：插件处理链面板 ──
        fxp = PluginPanel(config=config, saver=saver,
                          get_processor=lambda: _state.processor,
                          get_devices=lambda: [mp._output_combo.itemData(i) or mp._output_combo.itemText(i)
                             for i in range(mp._output_combo.count())],
                          logger=logger)

        # 展开对话框路由：EQ → 曲线编辑器；TSE → 参考录音弹框
        def open_eq_editor():
            from dialog_eq import EQCurveWidget
            gains = config.get("eq_current_gains", [0.0] * 61)
            dlg = QDialog(mp)
            dlg.setWindowTitle("均衡器")
            lay = QVBoxLayout(dlg)
            curve = EQCurveWidget()
            curve.set_gains(list(gains))

            def on_change(g):
                config.set("eq_current_gains", list(g))
                saver.request_save() if saver else config.save_config()
                proc = _state.processor
                if proc:
                    try:
                        proc.set_eq_gains(g)
                    except Exception:
                        pass

            curve.gains_changed.connect(on_change)
            lay.addWidget(curve, 1)
            row = QHBoxLayout()
            reset_btn = QPushButton("重置")

            def on_reset():
                curve.set_gains([0.0] * 61)

            reset_btn.clicked.connect(on_reset)
            row.addWidget(reset_btn)
            row.addStretch()
            ok_btn = QPushButton("确定")
            ok_btn.clicked.connect(dlg.accept)
            row.addWidget(ok_btn)
            lay.addLayout(row)
            dlg.resize(560, 320)
            dlg.exec()

        def open_tse_dialog():
            mp._open_ref_dialog()

        fxp._open_eq_editor = open_eq_editor
        fxp._open_tse_dialog = open_tse_dialog

        # ── 主容器：左=控制+频谱，右=插件链 ──
        main_container = QWidget()
        main_layout = QHBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(left_col, 0)
        from PySide6.QtWidgets import QFrame
        vsep = QFrame()
        vsep.setFrameShape(QFrame.VLine)
        vsep.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(vsep)
        fxp.setFixedWidth(_state._fx_w)
        main_layout.addWidget(fxp, 0)

        window.add_widget(main_container, stretch=0)
        _state.main_panel = mp
        _state.fx_panel = fxp
        _state.vu_meter = mp._vu_panel
        mp.set_get_processing_thread_callback(lambda: _state.processing_thread)

        import audio_processor
        audio_processor.set_module_log(logger)

    def _setup(self, window, res, config):
        _state.model_path = os.path.join(res, DENOISE_MODEL)
        ion = os.path.join(res, "audio_icon_on.ico")
        ioff = os.path.join(res, "audio_icon_off.ico")
        _state.icon_on_path = ion
        _state.icon_off_path = ioff
        if os.path.exists(ioff):
            try:
                window.setWindowIcon(QIcon(ioff))
            except:
                pass
        return ion, ioff

    def _auto_start(self):
        if _state.config.get("auto_start", False):
            QTimer.singleShot(500, lambda: start_processing(_state, _state.logger))

    def _check_vbcable(self):
        """启动后检测 VB-CABLE（仅 Windows）：开启检测时才检查，
        只有未安装才弹面板；已安装则无事发生。"""
        if not IS_WINDOWS or not _state.config:
            return
        if not _state.config.get("vbcable_check_enabled", True):
            return
        from dialog_vbcable_check import show_vbcable_dialog, vbcable_installed
        if vbcable_installed():
            return
        show_vbcable_dialog(_state.config)
        QTimer.singleShot(1000, lambda: _state.main_panel.refresh_devices()
                          if _state.main_panel else None)

    def _register_hotkey(self, logger):
        """通过原生事件过滤器注册全局热键（右Alt + >，仅 Windows；其它平台跳过）。"""
        if not IS_WINDOWS:
            logger.sys("全局热键仅 Windows 支持，已跳过")
            return
        from PySide6.QtCore import QAbstractNativeEventFilter
        import ctypes.wintypes

        MOD_ALT = 0x0001
        MOD_NOREPEAT = 0x4000
        VK_PERIOD = 0xBE  # 小数点键
        WM_HOTKEY = 0x0312
        hk_id = 9999

        mod = MOD_ALT | MOD_NOREPEAT
        if not ctypes.windll.user32.RegisterHotKey(None, hk_id, mod, VK_PERIOD):
            logger.sys("热键注册失败（可能被其他程序占用）")
            return

        class _HotkeyFilter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, eventType, message):
                if eventType == b"windows_generic_MSG":
                    msg = ctypes.wintypes.MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY and msg.wParam == hk_id:
                        if _state.config and _state.config.get("hotkey_enabled", True):
                            QTimer.singleShot(0, lambda: toggle_processing(_state, _state.logger))
                        return True, 0
                return False, 0

        self._hk_filter = _HotkeyFilter()
        QApplication.instance().installNativeEventFilter(self._hk_filter)
        logger.sys("热键已注册 (右Alt + >)")

    def run(self):
        # Qt 高 DPI 设置
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

        app = QApplication(sys.argv)
        app.setStyle("windows11")

        config = self._init_config()
        _state.config = config
        self._apply_style()

        # 启动时根据配置应用主题（palette 在窗口创建后由 _sync_theme_ui 统一处理）

        logger = Logger()
        _state.logger = logger
        QTimer.singleShot(3000, lambda: add_firewall_rule(logger))
        saver = DebouncedSaver(config)
        _state.debounced_saver = saver

        window = MainWindow(config, logger)
        _state.root = window
        self._window = window
        self._create_menu(window, config, logger)

        # 统一同步主题（palette + 标题栏 + 菜单栏）
        _sync_theme_ui(app, config)

        res = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        ion, ioff = self._setup(window, res, config)
        self._create_ui(window, config, saver, logger)

        if QSystemTrayIcon.isSystemTrayAvailable():
            tray = QSystemTrayIcon(QIcon(ioff), window)
            menu = QMenu()
            menu.setStyleSheet("""
                QMenu {                  font-size: 11pt; min-width: 120px; }
                QMenu::item { padding: 6px 24px; min-height: 22px; }
            """)
            menu.addAction("退出", lambda: quit_app(window))
            tray.setContextMenu(menu)

            def _on_tray_activated(reason):
                if reason == QSystemTrayIcon.Trigger:
                    if window.isVisible():
                        window.hide()
                    else:
                        window.show()
                        window.activateWindow()

            tray.activated.connect(_on_tray_activated)
            tray.show()
            _state.tray_icon = tray

        self._register_hotkey(logger)
        # 初始尺寸：左列（控制+频谱）+ 右列（插件面板）
        window.setFixedSize(_state._panel_w + _state._fx_w + 20, 640)


        if config.get("auto_start", False):
            window.hide()
        else:
            window.show()

        QTimer.singleShot(1000, self._auto_start)
        QTimer.singleShot(500, self._check_vbcable)
        logger.sys("就绪")
        sys.exit(app.exec())


# ── 工具函数 ──

def run_as_admin(cmd, logger):
    return _sys_run_as_admin(cmd, logger)

def add_registry(logger):
    return enable_autostart(logger)

def remove_registry(logger):
    return disable_autostart(logger)

def toggle_auto_start(config, logger):
    new = not config.get("auto_start", False)
    config.set("auto_start", new)
    config.save_config()
    logger.sys(f"自动启动: {'开' if new else '关'}")

def toggle_hotkey(config, logger):
    new = not config.get("hotkey_enabled", True)
    config.set("hotkey_enabled", new)
    config.save_config()
    logger.sys(f"快捷键: {'开' if new else '关'}")

def open_sound_panel(logger):
    _sys_open_sound_panel(logger)

def stop_processing_for_update(state, logger):
    if state.is_processing:
        logger.msg("配置更新，停止处理...")
        stop_processing(state, logger)

def restart(state, logger):
    if getattr(restart, '_pending', False):
        return
    restart._pending = True
    try:
        if state.is_processing:
            stop_processing(state, logger)
            start_processing(state, logger)
    finally:
        restart._pending = False

def add_firewall_rule(logger):
    return _sys_add_firewall(logger)

def run_app():
    MainApp().run()


if __name__ == "__main__":
    # 单实例锁（Windows 命名 Mutex / Linux flock，见 platform.system.acquire_single_instance）
    if not acquire_single_instance("PureVox"):
        print('程序已在运行')
        sys.exit(1)
    run_app()
