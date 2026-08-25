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
EQ 均衡器控件
"""

import math
from typing import Any, Callable, List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PySide6.QtCore import Qt, Signal, QRectF, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont

# 频点栅格 / Q / 频响计算一律取自引擎（单一实现来源）
from pvengine.components.eq import EQ_FREQS, response_at

PRESETS = {
    "默认平直": [0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0, 0,0,0,0,0,0,0,0,0,0,0],
    "清晰透亮": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,2,1,0,0,0,0,0,0,0,0,0,0,0],
    "温暖饱满": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,2,4,3,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,-1,-1,0,0,0,0,0,0,0],
    "低沉有力": [-4,0,-3,0,-2,0,-2,0,-1,0,1,4,6,4,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    "减少齿音": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,3,0,-15,0,3,0,0,0,0,0,0,0,0],
    "减少鼻音": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2,0,-14,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    "消除沉闷": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,0,0,0,2,0,-12,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
    "增强临场": [-4,0,-3,0,-2,0,-2,0,-1,0,-1,0,-1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,3,4,3,1,0,0,0,0,0,0,1,2,1,0,0,0,0,0],
}


def format_freq(freq):
    if freq >= 10000:
        return f"{round(freq / 1000)}k"
    if freq >= 1000:
        v = freq / 1000
        return f"{int(v)}k" if v == int(v) else f"{v:.1f}k"
    return f"{int(freq)}"


class EQCurveWidget(QWidget):
    gains_changed = Signal(list)
    Y_RANGE = 30
    Y_LIMIT = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = [0.0] * len(EQ_FREQS)
        self._dragging = None
        self.setMinimumHeight(150)
        self.setMinimumWidth(280)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        try:
            w, h = self.width(), self.height()
            L, R, T, B = 22, 10, 12, 18
            gw, gh = w - L - R, h - T - B

            pal = self.palette()
            bg_color = pal.base().color()
            text_color = pal.text().color()
            tc = text_color
            num_color = pal.placeholderText().color()
            from theme_colors import current_colors
            tc_colors = current_colors()
            grid_color = QColor(tc_colors.eq_grid)

            p.fillRect(0, 0, w, h, bg_color)

            # ── 网格横线（每10dB），0.5px 统一 ──
            for db in range(-self.Y_RANGE, self.Y_RANGE + 1, 10):
                y = T + gh / 2 - (db / self.Y_RANGE) * (gh / 2)
                p.setPen(QPen(grid_color, 0.5))
                p.drawLine(L, int(y), w - R, int(y))
                p.setPen(QPen(num_color))
                p.setFont(QFont("Microsoft YaHei", 5))
                p.drawText(0, int(y - 7), L - 4, 14, Qt.AlignRight | Qt.AlignVCenter, str(db))

            # ── 0dB 中线，稍粗 ──
            y0 = T + gh / 2
            p.setPen(QPen(grid_color, 1))
            p.drawLine(L, int(y0), w - R, int(y0))

            # ── 频段竖线，0.5px 统一 ──
            n_bands = len(EQ_FREQS)
            label_step = 3 if n_bands <= 31 else 5
            for i, freq in enumerate(EQ_FREQS):
                x = L + self._freq_x(freq, gw)
                p.setPen(QPen(grid_color, 0.5))
                p.drawLine(int(x), T, int(x), h - B)
                if i % label_step == 0:
                    p.setPen(QPen(num_color))
                    p.setFont(QFont("Microsoft YaHei", 5))
                    p.drawText(int(x - 12), T - 12, 24, 10, Qt.AlignCenter, format_freq(freq))

            accent = pal.highlight().color()

            # ── 真实响应曲线（200 点 _response 计算）──
            from PySide6.QtGui import QPainterPath
            curve_pen = QPen(accent, 2.5)
            p.setPen(curve_pen)
            path = QPainterPath()
            first = True
            for i in range(200):
                freq = 20 * (10 ** (i / 199 * 3))
                resp = self._response(freq)
                x = L + self._freq_x(freq, gw)
                y = T + gh / 2 - (resp / self.Y_RANGE) * (gh / 2)
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            p.drawPath(path)

            # ── 标记圆点 ──
            n_bands = len(EQ_FREQS)
            for idx, (freq, gain) in enumerate(zip(EQ_FREQS, self._values)):
                x = L + self._freq_x(freq, gw)
                y = T + gh / 2 - (gain / self.Y_RANGE) * (gh / 2)
                r = max(3.0, int(4.5 * w / 800))
                if gain > 0:
                    dot = QColor(accent.red(), accent.green(), accent.blue(), 200)
                elif gain < 0:
                    dot = QColor(accent.red(), accent.green(), accent.blue(), 120)
                else:
                    dot = QColor(tc.red(), tc.green(), tc.blue(), 60)
                p.setPen(Qt.NoPen)
                p.setBrush(dot)
                p.drawEllipse(int(x - r), int(y - r), int(r * 2), int(r * 2))
                if gain != 0:
                    p.setPen(num_color)
                    p.setFont(QFont("Microsoft YaHei", 5))
                    p.drawText(QRectF(x - 10, h - B + 1, 20, 10), Qt.AlignCenter, f"{gain:+.0f}")
        finally:
            p.end()

    def _freq_x(self, freq, gw):
        m = 0.03
        u = gw * (1 - 2 * m)
        n = (math.log10(freq) - math.log10(20)) / (math.log10(20000) - math.log10(20))
        return gw * m + n * u

    def _band_at(self, x):
        w = self.width()
        L, R = 28, 10
        gw = w - L - R
        x = max(L, min(w - R, x)) - L
        m = 0.03
        u = gw * (1 - 2 * m)
        n = (x - gw * m) / u
        freq = 10 ** (math.log10(20) + n * (math.log10(20000) - math.log10(20)))
        return min(range(len(EQ_FREQS)), key=lambda i: abs(math.log10(freq) - math.log10(EQ_FREQS[i])))

    def _response(self, freq):
        return response_at(freq, self._values)

    def _emit_debounced(self):
        """防抖发射：合并 50ms 内的多次变更，只发一次信号。"""
        if not hasattr(self, '_emit_timer'):
            self._emit_timer = QTimer(self)
            self._emit_timer.setSingleShot(True)
            self._emit_timer.timeout.connect(self._do_emit)
        self._emit_timer.start(50)

    def _do_emit(self):
        self.gains_changed.emit(self._values[:])

    def mousePressEvent(self, e):
        w = self.width()
        L, R = 28, 10
        T, B = 18, 32
        if e.x() < L or e.x() > w - R or e.y() < T or e.y() > self.height() - B:
            return
        gw = w - L - R
        mx = e.x() - L
        m = 0.03
        u = gw * (1 - 2 * m)
        n = (mx - gw * m) / u
        freq = 10 ** (math.log10(20) + max(0, min(1, n)) * (math.log10(20000) - math.log10(20)))
        self._drag_origin_y = e.y()
        self._drag_origin_vals = self._values[:]
        self._drag_freq = freq
        def _log_dist(i): return abs(math.log10(EQ_FREQS[i]) - math.log10(freq))
        ranked = sorted(range(len(EQ_FREQS)), key=_log_dist)
        b0, b1 = ranked[0], ranked[1] if len(ranked) > 1 else ranked[0]
        d0, d1 = _log_dist(b0), _log_dist(b1)
        total = d0 + d1
        if total < 1e-9:
            self._drag_bands = [(b0, 1.0)]
        else:
            self._drag_bands = [(b0, d1 / total), (b1, d0 / total)]
        self._dragging = True

    def mouseMoveEvent(self, e):
        if getattr(self, '_dragging', False):
            h = self.height()
            T, B = 18, 32
            gh = h - T - B
            delta_y = self._drag_origin_y - e.y()
            delta_gain = (delta_y / (gh / 2)) * self.Y_RANGE
            changed = False
            for band_idx, weight in self._drag_bands:
                orig = self._drag_origin_vals[band_idx]
                new_val = max(-self.Y_LIMIT,
                    min(self.Y_LIMIT, round(orig + weight * delta_gain)))
                if new_val != self._values[band_idx]:
                    self._values[band_idx] = new_val
                    changed = True
            if changed:
                self.repaint()
                self._emit_debounced()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        if hasattr(self, '_drag_bands'):
            del self._drag_bands

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if not d:
            return
        w = self.width()
        L, R = 28, 10
        T, B = 18, 32
        pos = e.position()
        if pos.x() < L or pos.x() > w - R or pos.y() < T or pos.y() > self.height() - B:
            return
        idx = self._band_at(int(pos.x()))
        # 累积滚动增量，通过 QTimer 防抖合并
        if not hasattr(self, '_wheel_acc'):
            self._wheel_acc = {}
        self._wheel_acc[idx] = self._wheel_acc.get(idx, 0) + (1 if d > 0 else -1)
        if not hasattr(self, '_wheel_timer') or self._wheel_timer is None:
            self._wheel_timer = QTimer(self)
            self._wheel_timer.setSingleShot(True)
            self._wheel_timer.timeout.connect(self._flush_wheel)
        self._wheel_timer.start(40)  # 40ms 内无滚轮事件则合并执行

    def _flush_wheel(self):
        acc = getattr(self, '_wheel_acc', {})
        self._wheel_acc = {}
        changed = False
        for idx, delta in acc.items():
            new = max(-self.Y_LIMIT, min(self.Y_LIMIT, round(self._values[idx] + delta)))
            if new != self._values[idx]:
                self._values[idx] = new
                changed = True
        if changed:
            self.repaint()
            self._emit_debounced()

    def get_gains(self):
        return self._values[:]

    def set_gains(self, gains):
        n = len(EQ_FREQS)
        if len(gains) == n:
            self._values = [round(g) for g in gains]
        else:
            self._values = [0.0] * n
        self.repaint()
        self.gains_changed.emit(self._values[:])

    def reset(self):
        self._values = [0.0] * len(EQ_FREQS)
        self.repaint()
        self.gains_changed.emit(self._values[:])

