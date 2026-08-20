# PureVox Lite — 美化托盘（纯 Tk，无系统托盘依赖）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later

import tkinter as tk
import os
import sys

BG = "#FFF8E1"
PANEL_BG = "#FFECB3"
BORDER = "#8D6E63"
TITLE_BG = "#6D4C41"
TITLE_FG = "#FFF8E1"
BTN_BG = "#FFB74D"
HOVER_BG = "#FFE0B2"
SELECT_BG = "#FFCC80"
FG = "#5D4037"

try:
    from ui import PIXEL_FONT
except Exception:
    PIXEL_FONT = "Microsoft YaHei"

class BeautifiedTray(tk.Toplevel):
    def __init__(self, master, cfg, on_gain, on_show, on_exit):
        super().__init__(master)
        self.cfg = cfg
        self.on_gain = on_gain
        self.on_show = on_show
        self.on_exit = on_exit
        self.overrideredirect(True)
        self.configure(bg=BORDER, bd=0)
        self.attributes("-topmost", True)
        self.geometry("240x100+0+0")
        self._place_br()
        self._dx = 0
        self._dy = 0
        self._bars = {}  # which -> list of seg frames

        bar = tk.Frame(self, bg=TITLE_BG, height=22)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        tk.Label(bar, text="◆ Lite 托盘", bg=TITLE_BG, fg=TITLE_FG, font=(PIXEL_FONT, 9, "bold")).pack(side=tk.LEFT, padx=6)
        tk.Button(bar, text="✕", bg="#E57373", fg="white", bd=0, relief=tk.FLAT, font=(PIXEL_FONT, 8), width=2, command=self._do_exit).pack(side=tk.RIGHT, padx=2)
        tk.Button(bar, text="□", bg=BORDER, fg=TITLE_FG, bd=0, relief=tk.FLAT, font=(PIXEL_FONT, 8), width=2, command=self.on_show).pack(side=tk.RIGHT)

        body = tk.Frame(self, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.pre_var = tk.StringVar(value=str(cfg.get("pre_gain_db", 0)))
        self.post_var = tk.StringVar(value=str(cfg.get("post_gain_db", 0)))
        for label, var, which in [("前", self.pre_var, "pre"), ("后", self.post_var, "post")]:
            row = tk.Frame(body, bg=BG)
            row.pack(fill=tk.X, pady=3, padx=4)
            tk.Label(row, text=label, bg=BG, fg=FG, width=2, font=(PIXEL_FONT, 9)).pack(side=tk.LEFT)
            bar_f = tk.Frame(row, bg=BORDER, height=10)
            bar_f.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
            segs = []
            for i in range(5):
                seg = tk.Frame(bar_f, bg=SELECT_BG if i < self._level(var.get()) else BG, width=14, height=10, bd=1, relief=tk.RAISED)
                seg.pack(side=tk.LEFT, padx=1, fill=tk.Y)
                seg.bind("<Button-1>", lambda e, w=which, lv=i: self._set_level(w, lv))
                segs.append(seg)
            self._bars[which] = segs
            tk.Button(row, text="−", bg=BTN_BG, fg=TITLE_BG, bd=1, relief=tk.RAISED, width=2, font=(PIXEL_FONT, 9, "bold"), command=lambda w=which: self._step(w, -1)).pack(side=tk.LEFT, padx=1)
            tk.Label(row, textvariable=var, bg=BG, fg=FG, width=3, font=(PIXEL_FONT, 9, "bold")).pack(side=tk.LEFT)
            tk.Button(row, text="+", bg=BTN_BG, fg=TITLE_BG, bd=1, relief=tk.RAISED, width=2, font=(PIXEL_FONT, 9, "bold"), command=lambda w=which: self._step(w, 1)).pack(side=tk.LEFT, padx=1)
            tk.Label(row, text="dB", bg=BG, fg=FG, font=(PIXEL_FONT, 8)).pack(side=tk.LEFT)

    def _level(self, val):
        try:
            v = int(float(val))
        except:
            v = 0
        return max(0, min(5, int((v + 20) / 10)))

    def _set_level(self, which, lv):
        v = lv * 10 - 20
        if which == "pre":
            self.pre_var.set(str(v))
            self.on_gain("pre", v)
            self.cfg["pre_gain_db"] = v
        else:
            self.post_var.set(str(v))
            self.on_gain("post", v)
            self.cfg["post_gain_db"] = v
        self._refresh(which)

    def _step(self, which, delta):
        var = self.pre_var if which == "pre" else self.post_var
        try:
            v = int(float(var.get()))
        except:
            v = 0
        v = max(-20, min(30, v + delta))
        var.set(str(v))
        self.on_gain(which, v)
        if which == "pre":
            self.cfg["pre_gain_db"] = v
        else:
            self.cfg["post_gain_db"] = v
        self._refresh(which)

    def _refresh(self, which=None):
        # 更新档位条颜色
        for w in ( [which] if which else ["pre", "post"] ):
            var = self.pre_var if w == "pre" else self.post_var
            lvl = self._level(var.get())
            for i, seg in enumerate(self._bars.get(w, [])):
                try:
                    seg.configure(bg=SELECT_BG if i < lvl else BG)
                except Exception:
                    pass

    def sync_from_cfg(self):
        # 主窗口增益变化时同步托盘显示
        try:
            self.pre_var.set(str(self.cfg.get("pre_gain_db", 0)))
            self.post_var.set(str(self.cfg.get("post_gain_db", 0)))
            self._refresh()
        except Exception:
            pass

    def _place_br(self):
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f"240x100+{sw-260}+{sh-140}")
        except Exception:
            pass

    def _drag_start(self, e):
        self._dx = e.x
        self._dy = e.y

    def _drag_move(self, e):
        try:
            x = self.winfo_x() + e.x - self._dx
            y = self.winfo_y() + e.y - self._dy
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _do_exit(self):
        self.on_exit()
