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

"""uitk 可视化组件：VU 电平表 / 频谱直方图（纯 tk Canvas 自绘）。

数据源与 PySide 版一致：AudioThread._vu_peak 峰值快照、
_spectrum_in/_spectrum_out 环形缓冲（read_latest 取最新 2048 样本）。
"""

import math
import tkinter as tk

from . import theme

# VU 分段色（亮/暗两态）
SEG_GREEN = "#66bb6a"
SEG_YELLOW = "#ffca28"
SEG_RED = "#f44336"
UNLIT = "#263238"


def db_from_peak(peak: float) -> float:
    return 20.0 * math.log10(max(peak, 1e-10))


class VUCanvas(tk.Canvas):
    """横向 LED 段式 VU 表：-60..0 dB，峰值保持指针。"""

    def __init__(self, parent, sizes=None, height=34):
        self.sizes = sizes
        super().__init__(parent, bg=theme.BASE, highlightthickness=0,
                         bd=0, height=height)
        self._db = -60.0
        self._smooth = -60.0   # 平滑电平（快攻慢放）
        self._peak_db = -60.0  # 峰值保持
        self._peak_hold_until = 0.0
        self.bind("<Configure>", lambda e: self.redraw())

    def update_level(self, peak: float, now: float):
        db = max(-60.0, min(0.0, db_from_peak(peak)))
        # 快攻慢放：上升立即，下降每帧 -1.5dB
        self._smooth = db if db > self._smooth else max(db, self._smooth - 1.5)
        if db > self._peak_db or now > self._peak_hold_until:
            self._peak_db = db
            self._peak_hold_until = now + 1.5
        self._db = db
        self.redraw()

    def redraw(self):
        w = max(self.winfo_width(), 40)
        h = max(self.winfo_height(), 16)
        self.delete("all")
        n_seg = 24
        pad = 2
        seg_w = (w - 2 * pad - (n_seg - 1)) // n_seg
        lit_frac = (self._smooth + 60.0) / 60.0
        peak_frac = (self._peak_db + 60.0) / 60.0
        y0, y1 = pad, h - pad - 4
        for i in range(n_seg):
            frac = (i + 1) / n_seg
            x0 = pad + i * (seg_w + 1)
            color = UNLIT
            if frac <= lit_frac:
                color = SEG_GREEN if frac < 0.6 else (
                    SEG_YELLOW if frac < 0.85 else SEG_RED)
            self.create_rectangle(x0, y0, x0 + seg_w, y1,
                                  fill=color, width=0)
        # 峰值保持指针
        px = pad + int(peak_frac * (w - 2 * pad))
        px = max(pad, min(w - pad - 1, px))
        self.create_rectangle(px - 1, y0, px + 1, y1,
                              fill=theme.TEXT, width=0)
        # dB 刻度
        self.create_text(pad, h - 4, text="-60", anchor="sw",
                         fill=theme.TEXT_FAINT,
                         font=("TkDefaultFont", 7))
        self.create_text(w - pad, h - 4, text="0dB", anchor="se",
                         fill=theme.TEXT_FAINT,
                         font=("TkDefaultFont", 7))


class SpectrumCanvas(tk.Canvas):
    """频谱直方图：输入（灰）+ 输出（绿）双柱，log 频轴 40Hz~20kHz。"""

    BARS = 32

    def __init__(self, parent, sizes=None, height=120):
        super().__init__(parent, bg=theme.BASE, highlightthickness=0,
                         bd=0, height=height)
        self._spec_in = [0.0] * self.BARS
        self._spec_out = [0.0] * self.BARS
        self.bind("<Configure>", lambda e: self.redraw())

    def update_spectrum(self, in_data, out_data):
        import numpy as np
        for target, data in ((self._spec_out, out_data),
                             (self._spec_in, in_data)):
            if data and len(data) >= 512:
                x = np.asarray(data[-2048:], dtype=np.float32)
                spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
                target[:] = self._log_bins(spec)
        self.redraw()

    @staticmethod
    def _log_bins(spec):
        """rFFT 幅度 → BARS 个 log 频段归一化幅值（40Hz~20kHz）。"""
        import numpy as np
        n = len(spec)
        freqs = np.fft.rfftfreq((n - 1) * 2, 1.0 / 48000.0)[:n]
        lo, hi = 40.0, 20000.0
        edges = np.logspace(math.log10(lo), math.log10(hi), SpectrumCanvas.BARS + 1)
        idx = np.searchsorted(freqs, edges)
        out = []
        peak_max = 1e-9
        for i in range(SpectrumCanvas.BARS):
            a, b = idx[i], max(idx[i + 1], idx[i] + 1)
            v = float(np.mean(spec[a:b]))
            out.append(v)
            peak_max = max(peak_max, v)
        # 相对当前帧最大值归一 + dB 压缩
        return [max(0.0, 1.0 + math.log10(v / peak_max) * 0.8)
                for v in out]

    def redraw(self):
        w = max(self.winfo_width(), 60)
        h = max(self.winfo_height(), 30)
        self.delete("all")
        n = self.BARS
        gap = 2
        bw = max(2, (w - 10 - (n - 1) * gap) // n)
        base_y = h - 12
        for i in range(n):
            x0 = 5 + i * (bw + gap)
            vi, vo = self._spec_in[i], self._spec_out[i]
            hi_bar = int(vo * (base_y - 4))
            self.create_rectangle(x0, base_y - hi_bar, x0 + bw, base_y,
                                  fill=theme.ACCENT if vo > 0.05 else UNLIT,
                                  width=0)
            # 输入谱画在输出下方细条（灰）叠加对比
            hb = int(vi * (base_y - 4))
            self.create_rectangle(x0, base_y - hb, x0 + bw,
                                  base_y - hb + 3, fill="#555555", width=0)
        self.create_line(5, base_y, w - 5, base_y, fill=theme.MID)
        for f, label in ((100, "100"), (1000, "1k"), (10000, "10k")):
            frac = math.log10(f / 40.0) / math.log10(20000.0 / 40.0)
            x = 5 + frac * (w - 10)
            self.create_text(x, h - 4, text=label, anchor="s",
                             fill=theme.TEXT_FAINT,
                             font=("TkDefaultFont", 7))


class LevelRing(tk.Canvas):
    """圆形运行指示灯：运行呼吸绿圈 / 停止灰圈。"""

    def __init__(self, parent, size=14):
        super().__init__(parent, bg=theme.WINDOW, width=size, height=size,
                         highlightthickness=0, bd=0)
        self._on = False
        self._size = size
        self._draw()

    def set_on(self, on):
        self._on = bool(on)
        self._draw()

    def _draw(self):
        s = self._size
        self.delete("all")
        color = theme.START_BG if self._on else theme.BUTTON
        outline = theme.MID if not self._on else theme.START_HOVER
        self.create_oval(2, 2, s - 2, s - 2, fill=color, outline=outline,
                         width=1)
