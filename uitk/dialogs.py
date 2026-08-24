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

"""uitk 对话框：关于（文档标签页）/ EQ 编辑器 / 简易确认。

关于对话框文本用 ast 从 dialog_about.py 源码提取字符串常量，
不导入该模块——保持 uitk 零 PySide6 依赖。
"""

import ast
import os
import tkinter as tk

from . import theme
from .metrics import make_sizes


def _extract_consts(names):
    """ast 解析 dialog_about.py，取模块级字符串常量。"""
    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "dialog_about.py")
    out = {}
    try:
        tree = ast.parse(open(src, encoding="utf-8").read())
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in names:
                        try:
                            out[t.id] = ast.literal_eval(node.value)
                        except Exception:
                            pass
    except Exception:
        pass
    return out


class DarkDialog(tk.Toplevel):
    """深色无边框弹窗：自绘标题栏（可拖动）+ 方形关闭钮，无系统标题栏。"""

    def __init__(self, parent, title, w, h, sizes=None, fonts=None):
        super().__init__(parent, bg=theme.WINDOW)
        self.sizes = sizes or make_sizes(100)
        self.fonts = fonts or {}
        self.title(title)
        self.withdraw()               # 先藏窗避免白闪
        self.overrideredirect(True)   # 去系统标题栏，与主窗风格一致
        # 尺寸随缩放挡位放大
        s = self.sizes["scale"]
        w, h = int(w * s), int(h * s)
        self.geometry(f"{w}x{h}")
        self._dlgw, self._dlgh = w, h
        bar = tk.Frame(self, bg=theme.BUTTON, height=self.sizes["titlebar_h"])
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        lbl = tk.Label(bar, text=title, bg=theme.BUTTON, fg=theme.TEXT,
                       font=self.fonts.get("bold"))
        lbl.pack(side=tk.LEFT, padx=self.sizes["pad_md"])
        # 方形关闭钮（与主窗同款：外壳锁正方形，× 居中）
        tb = self.sizes["titlebar_h"]
        wrap = tk.Frame(bar, bg=theme.BUTTON, width=tb, height=tb)
        wrap.pack(side=tk.RIGHT)
        wrap.pack_propagate(False)
        x = tk.Label(wrap, text="×", bg=theme.BUTTON, fg=theme.TEXT_DIM,
                     font=self.fonts.get("bold"), cursor="hand2")
        x.place(relx=0.5, rely=0.5, anchor="center")
        x.bind("<Button-1>", lambda e: self.destroy())
        x.bind("<Enter>", lambda e: (x.configure(bg=theme.STOP_BG, fg="#000000"),
                                     wrap.configure(bg=theme.STOP_BG)))
        x.bind("<Leave>", lambda e: (x.configure(bg=theme.BUTTON, fg=theme.TEXT_DIM),
                                     wrap.configure(bg=theme.BUTTON)))
        # 标题栏整体/文字可拖动
        for wd in (bar, lbl):
            wd.bind("<ButtonPress-1>", self._drag_begin)
            wd.bind("<B1-Motion>", self._drag_move)
        self._tdx = self._tdy = 0
        self.body = tk.Frame(self, bg=theme.WINDOW)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.transient(parent)
        # 弹出位置：主窗口居中（拿不到主窗几何时回退屏幕居中）
        self.update_idletasks()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            pos_x = px + max(0, (pw - self._dlgw) // 2)
            pos_y = py + max(0, (ph - self._dlgh) // 3)
        except Exception:
            pos_x = pos_y = 60
        self.geometry(f"+{pos_x}+{pos_y}")
        self.deiconify()
        self.lift()
        self.focus_force()

    def _drag_begin(self, e):
        self._tdx, self._tdy = e.x, e.y

    def _drag_move(self, e):
        try:
            x = self.winfo_x() + e.x - self._tdx
            y = self.winfo_y() + e.y - self._tdy
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass


def _md_to_text_widget(parent, md_text, fonts):
    """极简 markdown → Text 控件：#/##/### 标题、- 列表、**粗体**去星号。"""
    txt = tk.Text(parent, bg=theme.BASE, fg=theme.TEXT, bd=0,
                  wrap="word", padx=12, pady=10, cursor="arrow",
                  font=fonts.get("body"))
    bar_bg = theme.DARK
    txt.configure(selectbackground=bar_bg)
    txt.tag_configure("h1", font=fonts.get("title"),
                      foreground=theme.ACCENT, spacing1=8, spacing3=4)
    txt.tag_configure("h2", font=fonts.get("bold"),
                      foreground=theme.TEXT, spacing1=10, spacing3=4)
    txt.tag_configure("li", lmargin1=14, lmargin2=14,
                      spacing3=2)
    txt.tag_configure("dim", foreground=theme.TEXT_DIM)
    for raw in md_text.splitlines():
        line = raw.replace("**", "")
        if line.startswith("### "):
            txt.insert("end", line[4:] + "\n", "h2")
        elif line.startswith("## "):
            txt.insert("end", line[3:] + "\n", "h2")
        elif line.startswith("# "):
            txt.insert("end", line[2:] + "\n", "h1")
        elif line.startswith("- "):
            txt.insert("end", "· " + line[2:] + "\n", "li")
        elif line.startswith("> "):
            txt.insert("end", line[2:] + "\n", ("li", "dim"))
        elif line.strip().startswith("<"):
            continue    # 跳过 HTML 片段行
        elif line.strip():
            txt.insert("end", line + "\n")
        else:
            txt.insert("end", "\n")
    txt.configure(state="disabled")
    return txt


def _scrollable_text(parent, md_text, fonts):
    """原生 Text + Scrollbar：宽度随窗口缩放（word wrap），无 canvas hack。"""
    frame = tk.Frame(parent, bg=theme.BASE)
    txt = _md_to_text_widget(frame, md_text, fonts)
    bar = tk.Scrollbar(frame, command=txt.yview,
                       troughcolor=theme.DARK, bg=theme.BUTTON,
                       activebackground=theme.ACCENT,
                       width=sizes_bar_width())
    txt.configure(yscrollcommand=bar.set)
    bar.pack(side=tk.RIGHT, fill=tk.Y)
    txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    return frame


def sizes_bar_width():
    return 10


def show_about_dialog(parent, sizes=None, fonts=None):
    consts = _extract_consts({"CHANGELOG_TEXT", "_WINDOWS_BODY",
                              "_LINUX_BODY"})
    dlg = DarkDialog(parent, "关于 PureVox", 680, 620,
                     sizes=sizes, fonts=fonts)
    dlg.minsize(480, 380)
    # 允许拉伸：body/canvas/Text 全部 fill+expand，文本框跟随窗口
    dlg.body.pack_configure(fill=tk.BOTH, expand=True)
    tabs = tk.Frame(dlg.body, bg=theme.WINDOW)
    tabs.pack(fill=tk.X)
    holder = tk.Frame(dlg.body, bg=theme.BASE)
    holder.pack(fill=tk.BOTH, expand=True)
    pages = [
        ("更新日志", consts.get("CHANGELOG_TEXT", "（缺失）")),
        ("Windows 使用", consts.get("_WINDOWS_BODY", "（缺失）")),
        ("Linux 使用", consts.get("_LINUX_BODY", "（缺失）")),
    ]
    cur = [None]

    def show(idx):
        if cur[0] == idx:
            return
        cur[0] = idx
        for w in holder.winfo_children():
            w.destroy()
        f = _scrollable_text(holder, pages[idx][1], fonts or {})
        f.pack(fill=tk.BOTH, expand=True)
        for i, b in enumerate(tab_btns):
            b.configure(bg=theme.DARK if i == idx else theme.WINDOW,
                        fg=theme.ACCENT if i == idx else theme.TEXT_DIM)

    tab_btns = []
    for i, (name, _t) in enumerate(pages):
        b = tk.Label(tabs, text=name, bg=theme.WINDOW, fg=theme.TEXT_DIM,
                     font=(fonts or {}).get("bold"), padx=10,
                     pady=sizes["pad_sm"] if sizes else 4, cursor="hand2")
        b.pack(side=tk.LEFT)
        b.bind("<Button-1>", lambda e, i=i: show(i))
        tab_btns.append(b)
    show(0)


# ── EQ 编辑器（10 段滑杆 → 插值为 61 带增益）──
EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def open_eq_editor(parent, get_gains, set_gains, sizes=None, fonts=None):
    """get_gains() -> list[61]；set_gains(list[61]) 应用并持久化。"""
    import math
    dlg = DarkDialog(parent, "均衡器", 420, 420, sizes=sizes, fonts=fonts)
    cur61 = list(get_gains())
    freqs61 = [20 * (1000 ** (i / 60.0)) for i in range(61)]

    def interp10(vals10):
        log10f = [math.log10(f) for f in freqs61]
        pts = [(math.log10(f), v) for f, v in zip(EQ_BANDS, vals10)]
        out = []
        for lf in log10f:
            if lf <= pts[0][0]:
                out.append(pts[0][1])
                continue
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                if a[0] <= lf <= b[0]:
                    t = (lf - a[0]) / (b[0] - a[0])
                    out.append(a[1] + (b[1] - a[1]) * t)
                    break
            else:
                out.append(pts[-1][1])
        return out

    def band_from_61(gains61):
        out = []
        for fb in EQ_BANDS:
            j = min(range(61), key=lambda i: abs(freqs61[i] - fb))
            out.append(float(gains61[j]))
        return out

    vars10 = []
    grid = tk.Frame(dlg.body, bg=theme.WINDOW)
    grid.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    init10 = band_from_61(cur61)
    for i, fb in enumerate(EQ_BANDS):
        c = tk.Frame(grid, bg=theme.WINDOW)
        c.grid(row=0, column=i, sticky="ns")
        lbl = f"{fb//1000}k" if fb >= 1000 else str(fb)
        tk.Label(c, text=lbl, bg=theme.WINDOW, fg=theme.TEXT_DIM,
                 font=(fonts or {}).get("small")).pack()
        var = tk.DoubleVar(value=max(-12.0, min(12.0, init10[i])))
        vars10.append(var)
        s = tk.Scale(c, variable=var, from_=12, to=-12, resolution=1,
                     showvalue=False, length=180, orient=tk.VERTICAL,
                     bg=theme.WINDOW, fg=theme.TEXT,
                     troughcolor=theme.DARK, highlightthickness=0, bd=0,
                     activebackground=theme.ACCENT)
        s.pack()
        val_lbl = tk.Label(c, text=f"{int(var.get()):+d}", bg=theme.WINDOW,
                           fg=theme.TEXT, font=(fonts or {}).get("small"))
        val_lbl.pack()

        def _on(v, vv=var, vl=val_lbl):
            vl.configure(text=f"{int(float(v)):+d}")
            apply_now()

    def apply_now():
        vals = [v.get() for v in vars10]
        set_gains(interp10(vals))

    row = tk.Frame(dlg.body, bg=theme.WINDOW)
    row.pack(fill=tk.X, padx=10, pady=(0, 10))
    PRESETS = {"平直": [0] * 10, "低音增强": [6, 5, 3, 1, 0, 0, 0, 0, 0, 0],
               "人声增强": [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
               "高音增强": [0, 0, 0, 0, 0, 1, 3, 5, 6, 6]}
    for name, vals in PRESETS.items():
        b = tk.Label(row, text=name, bg=theme.BUTTON, fg=theme.TEXT,
                     font=(fonts or {}).get("small"), padx=8,
                     pady=2, cursor="hand2")
        b.pack(side=tk.LEFT, padx=2)
        b.bind("<Button-1>", lambda e, vs=vals: [
            v.set(x) for v, x in zip(vars10, vs)] + [apply_now()])


# ── TSE 参考录音 ──

def open_tse_dialog(parent, engine, config, sizes=None, fonts=None):
    """TSE 参考音频：显示当前参考状态；运行中可录 10s 参考并即时生效。

    依赖引擎已启动（recording_hook 由处理线程喂采样）。
    """
    import os
    import time as _t
    from tkinter import messagebox
    from audio_processor import (get_tse_recorder, RECORD_DURATION,
                                 _samples_to_wav_bytes, load_tse_reference,
                                 CFG_REF_WAV_PATH)
    from user_paths import WAV_PATH

    dlg = DarkDialog(parent, "目标说话人 TSE · 参考音频", 380, 200,
                     sizes=sizes, fonts=fonts)
    info = tk.Label(dlg.body, text="", bg=theme.WINDOW, fg=theme.TEXT_DIM,
                    font=(fonts or {}).get("body"), justify="left",
                    anchor="w")
    info.pack(fill=tk.X, padx=14, pady=(10, 4))
    status_lbl = tk.Label(dlg.body, text="", bg=theme.WINDOW,
                          fg=theme.ACCENT, font=(fonts or {}).get("bold"))
    status_lbl.pack(fill=tk.X, padx=14)

    wav = config.get(CFG_REF_WAV_PATH, "") if config else ""
    if wav and os.path.exists(wav):
        kb = os.path.getsize(wav) / 1024
        mt = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(os.path.getmtime(wav)))
        info.configure(text=f"已有参考：{os.path.basename(wav)}\n"
                            f"{kb:.0f} KB · {mt}")
    else:
        info.configure(text="尚无参考音频——TSE 插件将直通。\n"
                            "启动音频处理后点「开始录音」，对麦克风说 10 秒话。")

    recording = [False]

    def do_record():
        th = engine.thread
        if th is None or not engine.running:
            messagebox.showinfo("PureVox", "请先启动音频处理，再录制参考。")
            return
        rec = get_tse_recorder()
        th.set_recording_hook(lambda s: rec.feed(list(s)))
        th.set_recording_enabled(True)
        recording[0] = True
        deadline = [RECORD_DURATION]

        def tick():
            if not recording[0]:
                return
            if deadline[0] > 0:
                status_lbl.configure(
                    text=f"录音中… {deadline[0]:.0f}s（请持续说话）")
                deadline[0] -= 1
                dlg.after(1000, tick)
                return
            finish()

        def finish():
            recording[0] = False
            try:
                th.set_recording_enabled(False)
            except Exception:
                pass
            raw = get_tse_recorder().wait_and_get()
            if not raw:
                status_lbl.configure(text="录音失败（未捕获到音频）")
                return
            try:
                with open(WAV_PATH, "wb") as f:
                    f.write(_samples_to_wav_bytes(raw))
            except Exception as e:
                status_lbl.configure(text=f"保存失败: {e}")
                return
            if config:
                config.set(CFG_REF_WAV_PATH, WAV_PATH)
                config.save_config()
            proc = engine.processor
            ok = load_tse_reference(proc, WAV_PATH) if proc else False
            status_lbl.configure(
                text="完成！参考已生效。" if ok
                else "已保存，但加载失败——重启音频处理后生效。")

        tick()

    btn_row = tk.Frame(dlg.body, bg=theme.WINDOW)
    btn_row.pack(fill=tk.X, padx=14, pady=8)
    rec_btn = tk.Label(btn_row, text="● 开始录音 (10s)", bg=theme.STOP_BG,
                       fg="#000000", font=(fonts or {}).get("bold"),
                       padx=12, pady=sizes["pad_sm"] if sizes else 4,
                       cursor="hand2")
    rec_btn.pack(side=tk.LEFT)
    rec_btn.bind("<Button-1>", lambda e: None if recording[0] else do_record())


# ── VB-CABLE 检测面板（Windows）──

def vbcable_installed() -> bool:
    """枚举音频端点找 CABLE（不导入 Qt 版检测模块）。"""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        try:
            for i in range(p.get_device_count()):
                name = str(p.get_device_info_by_index(i).get("name", ""))
                if "cable" in name.lower():
                    return True
        finally:
            p.terminate()
    except Exception:
        pass
    return False


def open_vbcable_dialog(parent, sizes=None, fonts=None):
    """对照 dialog_vbcable_check：状态灯 + 双端点说明 + 驱动卡片（三操作）。"""
    dlg = DarkDialog(parent, "VB-CABLE 虚拟声卡检测", 420, 320,
                     sizes=sizes, fonts=fonts)
    installed = vbcable_installed()
    color = "#3aa76d" if installed else "#d9534f"
    S = sizes or make_sizes(100)
    F = fonts or {}

    # ── 状态行 ──
    head = tk.Frame(dlg.body, bg=theme.WINDOW)
    head.pack(fill=tk.X, padx=16, pady=(14, 4))
    dot = tk.Canvas(head, bg=theme.WINDOW, width=14, height=14,
                    highlightthickness=0)
    dot.pack(side=tk.LEFT, padx=(0, 8))
    dot.create_oval(1, 1, 13, 13, fill=color, outline="")
    tk.Label(head, text=f"VB-CABLE 驱动：{installed and '已安装' or '未安装'}",
             bg=theme.WINDOW, fg=theme.TEXT,
             font=F.get("bold")).pack(side=tk.LEFT)

    # ── 双端点说明 ──
    tk.Label(dlg.body,
             text="CABLE Input —— 播放设备，PureVox 的「音频输出」选它；\n"
                  "CABLE Output —— 录制设备，即其他软件里的虚拟麦克风。\n"
                  "两端均建议固定 48kHz（声音控制面板 → 属性 → 高级）。",
             bg=theme.BASE, fg=theme.TEXT_DIM, font=F.get("body"),
             justify="left", anchor="w", padx=12, pady=8).pack(
        fill=tk.X, padx=16, pady=4)

    # ── 驱动卡片：三操作按钮行为与 PySide 版一致 ──
    card = tk.Frame(dlg.body, bg=theme.ALT_BASE)
    card.pack(fill=tk.X, padx=16, pady=10)
    actions = []
    if installed:
        tip = "已安装，可直接使用；如需重装或改采样率再打开控制面板。"
        actions.append(("打开控制面板", _open_vbcable_cp))
    else:
        tip = "未安装——无法向 OBS 等软件提供虚拟麦克风输入。"
    actions += [
        ("下载驱动包", lambda: _open_url(
            "https://download.vb-audio.com/Download_CABLE/"
            "VBCABLE_Driver_Pack45.zip")),
        ("视频教程", lambda: _open_url(
            "https://www.bilibili.com/video/BV1i2bazGEKe/")),
    ]
    tk.Label(card, text="驱动", bg=theme.ALT_BASE, fg=theme.TEXT_FAINT,
             font=F.get("small")).pack(anchor="w", padx=10,
                                       pady=(8, 0))
    btn_row = tk.Frame(card, bg=theme.ALT_BASE)
    btn_row.pack(fill=tk.X, padx=10, pady=(4, 10))
    for text, cmd in actions:
        b = tk.Label(btn_row, text=text, bg=theme.BUTTON, fg=theme.ACCENT,
                     font=F.get("body"), padx=12, cursor="hand2",
                     pady=S["pad_sm"])
        b.pack(side=tk.LEFT, padx=(0, 8))
        b.bind("<Button-1>", lambda e, c=cmd: c())
        b.bind("<Enter>", lambda e, w=b: w.configure(bg=theme.DARK))
        b.bind("<Leave>", lambda e, w=b: w.configure(bg=theme.BUTTON))
    tk.Label(dlg.body, text=tip, bg=theme.WINDOW, fg=theme.TEXT_FAINT,
             font=F.get("small"), justify="left", wraplength=380).pack(
        anchor="w", padx=16, pady=(0, 10))


def _open_vbcable_cp():
    """打开 VB-CABLE 控制面板（驱动级配置需管理员权限，UAC 提权）。"""
    import ctypes
    import os
    for p in (r"C:\Program Files\VB\CABLE\VBCABLE_ControlPanel.exe",
              r"C:\Program Files (x86)\VB\CABLE\VBCABLE_ControlPanel.exe"):
        if os.path.exists(p):
            try:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", p,
                                                    None, None, 1)
                return
            except Exception:
                pass
    _open_url("ms-settings:sound")


def _open_url(url):
    import webbrowser
    if url.startswith("ms-settings") or url.startswith("http"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
