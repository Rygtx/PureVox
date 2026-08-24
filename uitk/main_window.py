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

"""uitk 主窗口：顶部工具条 + 单列节点面板，接真实 plugin_chain。

数据流（DESIGN.md §7）：config.plugin_chain（type/enabled/params）
↔ NodeRow 双向绑定；任何增删/排序/开关/参数变化即持久化。
节点类型清单唯一来源 = pvengine.plugins.all_specs()，UI 禁止自建。
"""

import sys
import time
import webbrowser as _webbrowser_mod


def webbrowser_open(url):
    try:
        _webbrowser_mod.open(url)
    except Exception:
        pass

import tkinter as tk
import tkinter.font as tkfont

from . import theme
from .metrics import make_sizes, detect_zoom_for_screen, \
    fix_tk_scaling, pick_font_family
from .widgets import FlatButton, DarkCheck, DarkCombo, ScrollFrame
from .engine import EngineController, enum_io_devices
from .viz import VUCanvas, SpectrumCanvas

KIND_LABELS = {"input": "输入", "output": "输出", "fx": "处理", "viz": "可视化"}
KIND_ORDER = ["input", "fx", "viz", "output"]
# 需要设备下拉的节点类型：input/output 选 device，echo_cancel 选 far_device
DEV_KEY = {"audio_input": ("device", "inputs"),
           "audio_output": ("device", "outputs"),
           "remote_mic": None,
           "virtual_output": ("device", "voutputs"),
           "echo_cancel": ("far_device", "outputs")}


class ParamSlider(tk.Frame):
    """行内参数滑杆：自绘 HSlider + 右侧大号数值（紧贴 × 前）。"""

    def __init__(self, parent, label, lo, hi, default, step,
                 sizes, fonts, on_commit):
        super().__init__(parent, bg=parent.cget("bg"))
        self.sizes = sizes
        self.fonts = fonts
        tk.Label(self, text=label, bg=self["bg"], fg=theme.TEXT_DIM,
                 font=fonts.get("small")).pack(side=tk.LEFT,
                                               padx=(0, sizes["pad_sm"]))
        # 数值在右（× 前），大号加粗醒目
        self.val_lbl = tk.Label(self, text=f"{default:g}", bg=self["bg"],
                                fg=theme.ACCENT, font=fonts.get("bold"),
                                width=5, anchor="e")
        self.val_lbl.pack(side=tk.RIGHT)
        from .widgets import HSlider
        ref = {}
        s = HSlider(self, lo, hi, default, step, sizes=sizes,
                    command=lambda: (
                        self.var.set(ref["s"].value),
                        self.val_lbl.configure(text=f"{ref['s'].value:g}"),
                        on_commit()))
        ref["s"] = s
        s.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var = tk.DoubleVar(value=float(default))
        s.bind("<ButtonRelease-1>", lambda e: on_commit())
        s.bind("<ButtonRelease-1>", lambda e: on_commit())


class NodeRow(tk.Frame):
    """节点行：手柄 + 名称 + 启用勾选 + 删除 + inline 参数区。

    手柄「‖」与删除「×」用像素字体渲染；手柄支持拖拽排序。
    """

    GRIP_GLYPH = "‖"
    CLOSE_GLYPH = "×"

    def __init__(self, parent, cfg, spec, sizes, fonts,
                 on_remove=None, on_toggle=None, on_drag_preview=None,
                 on_drag_commit=None, on_param=None):
        self.sizes = sizes
        self.fonts = fonts
        self._on_drag_preview = on_drag_preview
        self._on_drag_commit = on_drag_commit
        self.cfg = cfg            # {type, enabled, params} 引用
        self.spec = spec
        super().__init__(parent.body, bg=theme.ALT_BASE, bd=0)
        head = tk.Frame(self, bg=theme.ALT_BASE)
        head.pack(fill=tk.X, padx=self.sizes["pad_md"], pady=2)
        self.head = head
        # 布局（左→右）：手柄 · 开关 · 名称 ······ 用户操作区（下拉/滑杆）· 删除 ×
        # 类型名不再占横向空间；中间全部让给用户操作控件
        self.grip = tk.Label(head, text=self.GRIP_GLYPH,
                             bg=theme.ALT_BASE, fg=theme.MID,
                             font=fonts.get("bold"), cursor="fleur")
        self.grip.pack(side=tk.LEFT, padx=(0, self.sizes["pad_sm"]))
        self.grip.bind("<ButtonPress-1>", self._drag_begin)
        self.grip.bind("<B1-Motion>", self._drag_motion)
        self.grip.bind("<ButtonRelease-1>", self._drag_release)
        self.on_var = tk.BooleanVar(value=bool(cfg.get("enabled", True)))
        self.check = DarkCheck(head, "", self.on_var, command=self._toggled,
                               sizes=sizes, fonts=fonts)
        self.check.pack(side=tk.LEFT, padx=(0, self.sizes["pad_sm"]))
        self.title_lbl = tk.Label(head, text=f"{spec.label}", bg=theme.ALT_BASE,
                                  fg=theme.TEXT, anchor="w",
                                  font=fonts.get("body"))
        self.title_lbl.pack(side=tk.LEFT, padx=(0, self.sizes["pad_sm"]))
        rm = tk.Label(head, text=self.CLOSE_GLYPH, bg=theme.ALT_BASE,
                      fg=theme.TEXT_DIM, font=fonts.get("bold"),
                      cursor="hand2")
        if on_remove:
            rm.bind("<Button-1>", lambda e: on_remove())
            rm.bind("<Enter>", lambda e: rm.configure(fg=theme.STOP_BG))
            rm.bind("<Leave>", lambda e: rm.configure(fg=theme.TEXT_DIM))
        rm.pack(side=tk.RIGHT)   # × 永远最后（最右）
        self.rm_lbl = rm
        # 中间操作区：设备下拉 / 单参数滑杆都放这里，吃掉全部剩余宽度
        self.mid = tk.Frame(head, bg=theme.ALT_BASE)
        self.mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                      padx=(0, self.sizes["pad_sm"]))
        self.dev_combo = None
        self._build_device_combo()
        # 多参数/编辑入口/viz 走下方参数区（始终显示）
        self.body_frame = tk.Frame(self, bg=theme.BASE)
        self._build_inline(on_param)
        self.ensure_body()

    def ensure_body(self):
        """参数区有内容即显示（无展开收起概念）。"""
        if self.body_frame.winfo_children():
            self.body_frame.pack(fill=tk.X, padx=self.sizes["pad_lg"],
                                 pady=(0, 4))

    def _dev_spec(self):
        return DEV_KEY.get(self.spec.name)

    def _build_device_combo(self):
        """设备下拉置于行头最右端（值存 params.device/far_device）。"""
        dspec = self._dev_spec()
        if not dspec:
            return
        key, _dir = dspec
        holder: dict = {}
        var = tk.StringVar(value=str((self.cfg.get("params") or {}).get(key, "")))
        self.dev_var = var
        self.dev_combo = DarkCombo(
            self.mid, [var.get()] if var.get() else ["（默认）"], var,
            on_change=lambda: self._on_dev_changed(holder, key),
            sizes=self.sizes, fonts=self.fonts)
        # 下拉置于中间操作区右缘（紧挨 ×）；inner 锁宽高（propagate 已关）
        self.dev_combo.inner.configure(
            width=int(self.sizes["win_w"] * 0.42),
            height=self.sizes["combo_h"])
        self.dev_combo.pack(side=tk.RIGHT, fill=tk.Y)
        holder["row"] = self

    def _on_dev_changed(self, holder, key):
        val = self.dev_var.get()
        if val in ("（默认）", "自动（默认物理扬声器）"):
            val = ""
        self.cfg.setdefault("params", {})[key] = val
        cb = getattr(self, "_on_param_cb", None)
        if cb:
            cb()

    def set_devices(self, devices):
        """刷新设备下拉（保持当前选择）。"""
        dspec = self._dev_spec()
        if not dspec or not hasattr(self, "dev_combo"):
            return
        _, direction = dspec
        if self.spec.name == "virtual_output":
            items = [t for t, _d in devices.get("voutputs", [])]
            cur = str((self.cfg.get("params") or {}).get(dspec[0], ""))
            self.dev_combo.set_values(items)
            # 未选或失效时自动选第一个 VB 端点（模糊匹配结果）
            if items and cur not in items:
                self.dev_var.set(items[0])
                self.cfg.setdefault("params", {})["device"] = items[0]
                cb = getattr(self, "_on_param_cb", None)
                if cb:
                    cb()
            return
        items = [t for t, _d in devices.get(direction, [])]
        if self.spec.name == "audio_input" and getattr(self, "_prefer_virtual", False):
            # 虚拟输入：从 CABLE 输入端点里选（模糊匹配，排除 16ch）
            vb = [t for t, _d in devices.get("vinputs", [])]
            if vb:
                self._prefer_virtual = False
                self.dev_combo.set_values(items)
                self.dev_var.set(vb[0])
                self.cfg.setdefault("params", {})["device"] = vb[0]
                cb = getattr(self, "_on_param_cb", None)
                if cb:
                    cb()
                return
        if self.spec.name == "echo_cancel":
            items = ["自动（默认物理扬声器）"] + items
        elif not items:
            items = ["（默认）"]
        saved_dev = str((self.cfg.get("params") or {}).get(dspec[0], ""))
        cur = saved_dev or ("自动（默认物理扬声器）"
                            if self.spec.name == "echo_cancel" else "")
        self.dev_combo.set_values(items)
        if cur in items:
            self.dev_var.set(cur)
        elif items:
            # 未选/失效：自动选第一个真实设备（空设备会被 SessionPlan 跳过）
            first = items[0] if self.spec.name != "echo_cancel" else items[0]
            self.dev_var.set(first)
            if self.spec.name != "echo_cancel":
                self.cfg.setdefault("params", {})[dspec[0]] = first
                cb = getattr(self, "_on_param_cb", None)
                if cb and first != saved_dev:
                    cb()

    def _build_inline(self, on_param):
        saved = self.cfg.get("params") or {}
        params = self.spec.params or {}
        # 单参数节点：滑杆直接放进行中间操作区（与下拉同一行）
        # 多参数/eq/tse/viz：走下方参数区
        inline_ok = (len(params) == 1
                     and self.spec.name not in ("eq", "tse")
                     and self.spec.kind != "viz")
        for key, pdef in params.items():
            label, lo, hi, default, step = pdef
            cur = saved.get(key, default)
            ref = {}
            ps = ParamSlider(
                self.mid if inline_ok else self.body_frame,
                label, lo, hi,
                default if cur is None else cur, step,
                self.sizes, self.fonts,
                on_commit=lambda k=key: (
                    on_param and on_param(
                        self, k, round(ref["s"].var.get(), 4))))
            ref["s"] = ps
            ps._key = key
            ps.pack(fill=tk.X, padx=self.sizes["pad_sm"],
                    pady=0 if inline_ok else 2)

    def _toggled(self):
        self.cfg["enabled"] = bool(self.on_var.get())
        if self._on_toggle_cb:
            self._on_toggle_cb()
        # 开关节点同步视觉弱化
        self._apply_enabled_look()

    _on_toggle_cb = None

    def set_toggle_cb(self, cb):
        self._on_toggle_cb = cb

    def _apply_enabled_look(self):
        fg = theme.TEXT if bool(self.on_var.get()) else theme.MID
        try:
            self.title_lbl.configure(fg=fg)
        except Exception:
            pass

    # ── 拖拽排序：拖动实时换位（直观），松手才持久化/热重建 ──
    def _drag_begin(self, e):
        self._drag_target = None

    def _drag_motion(self, e):
        if self._on_drag_preview is None:
            return
        target = None
        for w in self.master.winfo_children():
            if isinstance(w, NodeRow) and w is not self:
                top = w.winfo_rooty()
                if top <= e.y_root < top + w.winfo_height():
                    target = w
                    break
        if target is not None and target is not self._drag_target:
            self._drag_target = target
            self._on_drag_preview(self, target)

    def _drag_release(self, _e):
        self._drag_target = None
        if self._on_drag_commit is not None:
            self._on_drag_commit()


class MainWindowTk:
    """主窗口：单一工具条 + 节点滚动面板（plugin_chain 持久化）。"""

    def __init__(self, zoom=None, config=None):
        from .metrics import enable_hidpi
        enable_hidpi()
        self.config = config
        self.root = tk.Tk()
        fix_tk_scaling(self.root)
        family = pick_font_family(self.root)
        self.zoom = zoom or detect_zoom_for_screen(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.sizes = make_sizes(self.zoom)
        S = self.sizes
        self.fonts = {
            "body": tkfont.Font(family=family, size=-S["font_body"]),
            "bold": tkfont.Font(family=family, size=-S["font_body"],
                                weight="bold"),
            "title": tkfont.Font(family=family, size=-S["font_title"],
                                 weight="bold"),
            "small": tkfont.Font(family=family, size=-S["font_small"]),
        }
        self.root.title("PureVox")
        self.root.configure(bg=theme.WINDOW)
        self.root.geometry(f"{S['win_w']}x{S['win_h']}")

        # ── 自绘顶栏（去系统标题栏，保证颜色一致；整体可拖动）──
        self.root.withdraw()   # 先藏窗，全部上色后再显示——避免白闪
        self.root.overrideredirect(True)
        bar_title = tk.Frame(self.root, bg=theme.BUTTON, height=S["titlebar_h"])
        bar_title.pack(fill=tk.X)
        bar_title.pack_propagate(False)
        title_lbl = tk.Label(bar_title, text="PureVox", bg=theme.BUTTON,
                             fg=theme.TEXT, font=self.fonts["bold"])
        title_lbl.pack(side=tk.LEFT, padx=S["pad_md"])
        # 关闭钮：外壳锁定正方形（titlebar 内切），按钮填满
        close_wrap = tk.Frame(bar_title, bg=theme.BUTTON,
                              width=S["titlebar_h"], height=S["titlebar_h"])
        close_wrap.pack(side=tk.RIGHT)
        close_wrap.pack_propagate(False)
        btn_x = tk.Label(close_wrap, text="×", bg=theme.BUTTON,
                         fg=theme.TEXT_DIM, font=self.fonts["bold"],
                         cursor="hand2")
        btn_x.place(relx=0.5, rely=0.5, anchor="center")
        btn_x.bind("<Button-1>", lambda e: (
            self._hide_window() if getattr(self, "tray", None)
            else self.quit_app()))
        btn_x.bind("<Enter>", lambda e: (btn_x.configure(
            bg=theme.STOP_BG, fg="#000000"), close_wrap.configure(bg=theme.STOP_BG)))
        btn_x.bind("<Leave>", lambda e: (btn_x.configure(
            bg=theme.BUTTON, fg=theme.TEXT_DIM), close_wrap.configure(bg=theme.BUTTON)))
        for w in (bar_title, title_lbl):
            w.bind("<ButtonPress-1>", self._title_drag_begin)
            w.bind("<B1-Motion>", self._title_drag_move)
        # 无边框窗口保留任务栏图标（WS_EX_APPWINDOW）
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) \
                or self.root.winfo_id()
            exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, -20, exstyle | 0x00040000)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, 0x0007)
        except Exception:
            pass

        # ── 工具条：启动 → 退出 → 添加 → 设置 ──
        bar = tk.Frame(self.root, bg=theme.WINDOW)
        bar.pack(fill=tk.X, padx=S["pad_md"], pady=S["pad_md"])
        self.btn_start = FlatButton(bar, "启动音频处理",
                                    command=self._on_start,
                                    bg=theme.START_BG, fg="#000000",
                                    font=self.fonts["body"], sizes=self.sizes)
        self.btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_quit = FlatButton(bar, "退出", command=self.quit_app,
                                   fg=theme.STOP_BG,
                                   font=self.fonts["body"],
                                   sizes=self.sizes, pad=S["pad_md"])
        self.btn_quit.pack(side=tk.LEFT, padx=(S["pad_sm"], 0))

        self.btn_add = FlatButton(bar, "添加 ▾", command=self._add_menu,
                                  font=self.fonts["body"], sizes=self.sizes,
                                  pad=S["pad_md"])
        self.btn_add.pack(side=tk.LEFT, padx=(S["pad_sm"], 0))

        self.btn_gear = FlatButton(bar, "设置 ▾", command=self._gear_menu,
                                   font=self.fonts["body"], sizes=self.sizes,
                                   pad=S["pad_md"])
        self.btn_gear.pack(side=tk.LEFT, padx=(S["pad_sm"], 0))

        # ── 节点面板（滚动）──
        self.panel = ScrollFrame(self.root, sizes=self.sizes, fonts=self.fonts)
        self.panel.pack(fill=tk.BOTH, expand=True,
                        padx=S["pad_md"], pady=(0, S["pad_md"]))
        self.rows: list[NodeRow] = []

        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind_all("<Button-4>", self._wheel)
        self.root.bind_all("<Button-5>", self._wheel)
        from logger import Logger
        self.engine = EngineController(Logger(), config=self.config)
        self._viz_widgets: list = []
        self.root.after(33, self._viz_tick)
        tkvar = tk.BooleanVar
        self._hotkey_var = tkvar(value=bool(self._cfg_get("hotkey_enabled", True)))
        self._autorun_var = tkvar(value=bool(self._cfg_get("auto_start", False)))
        boot = False
        try:
            from pvplatform.system import is_autostart
            boot = is_autostart()
        except Exception:
            pass
        self._boot_var = tkvar(value=bool(boot))
        self._setup_tray()
        self._setup_hotkey()
        # 全部控件上色完成后一次性显示——消除启动白闪；屏幕居中
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww, hh = S["win_w"], S["win_h"]
        self.root.geometry(f"+{(sw - ww) // 2}+{(sh - hh) // 2}")
        self.root.deiconify()

    # ── 托盘（动作经队列转投主线程）──
    def _setup_tray(self):
        import os
        from collections import deque
        from .tray import create_tray
        self._tray_actions = deque()
        res = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        icon_on = os.path.join(res, "assets", "icons", "audio_icon_on.ico")
        icon_off = os.path.join(res, "assets", "icons", "audio_icon_off.ico")
        self.tray = create_tray(
            icon_on, icon_off,
            on_toggle=lambda: self._tray_actions.append("toggle"),
            on_quit=lambda: self._tray_actions.append("quit"))
        if self.tray:
            self._poll_tray()

    def _poll_tray(self):
        while getattr(self, "_tray_actions", None):
            try:
                act = self._tray_actions.popleft()
            except IndexError:
                break
            if act == "toggle":
                self.toggle_window()
            elif act == "quit":
                self.quit_app()
                return
        self.root.after(120, self._poll_tray)

    def toggle_window(self):
        """显隐切换：overrideredirect 窗口的 state() 不可靠，用自维护标志。"""
        shown = getattr(self, "_shown", True)
        if shown:
            self._hide_window()
        else:
            self.root.deiconify()
            # 无边框窗口 deiconify 后可能不置顶/无焦点，补一次提升
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)
            self.root.after(50, lambda: self.root.attributes("-topmost", False))
            self._shown = True

    def _hide_window(self):
        self.root.withdraw()
        self._shown = False

    def quit_app(self):
        """完整退出：停引擎 → 删托盘图标 → 关窗口。"""
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            if getattr(self, "tray", None):
                self.tray.remove()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _title_drag_begin(self, e):
        self._tdx, self._tdy = e.x, e.y

    def _title_drag_move(self, e):
        try:
            x = self.root.winfo_x() + e.x - self._tdx
            y = self.root.winfo_y() + e.y - self._tdy
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ── 链 ↔ 配置 ──
    def load_chain(self, chain_cfg):
        from pvengine.plugins import get_spec
        self.clear_rows()
        for item in chain_cfg:
            t = str(item.get("type", ""))
            spec = get_spec(t)
            if spec is None:
                continue
            self._make_row(dict(item), spec)

    def to_config(self):
        return [dict(r.cfg) for r in self.rows]

    def _persist(self):
        if not self.config:
            return
        try:
            self.config.set("plugin_chain", self.to_config())
            self.config.save_config()
        except Exception:
            pass

    def _apply_chain_change(self):
        """结构变更（增删/排序/开关）→ 持久化；运行中则热重建音频链。"""
        was_running = self.engine.running
        self._persist()
        if not was_running:
            return
        self.engine.stop()
        err = self.engine.start(self.to_config())
        if err:
            from tkinter import messagebox
            messagebox.showwarning("PureVox", f"链已更新，但重启失败：\n{err}")
            self._set_running_ui(False)
        else:
            self._set_running_ui(True)

    # ── 工具条行为 ──
    def _on_start(self):
        if self.engine.running:
            self.engine.stop()
            self._set_running_ui(False)
            self.refresh_devices()
            return
        err = self.engine.start(self.to_config())
        if err:
            if "48kHz" in err:
                self._warn_48k(err)
            else:
                from tkinter import messagebox
                messagebox.showwarning("PureVox", err)
            self._set_running_ui(False)
            return
        self._set_running_ui(True)

    def _warn_48k(self, err):
        """48k 检测失败弹窗：逐设备列出原因（WASAPI 严格语义）。"""
        from .dialogs import DarkDialog
        detail = err.split("：", 1)[-1]
        dlg = DarkDialog(self.root, "48kHz 检测未通过", 400, 220,
                         sizes=self.sizes, fonts=self.fonts)
        tk.Label(dlg.body, text="以下设备无法以 48kHz 打开，已阻止启动：",
                 bg=theme.WINDOW, fg=theme.TEXT,
                 font=self.fonts.get("bold"),
                 justify="left", anchor="w").pack(
            fill=tk.X, padx=14, pady=(10, 4))
        tk.Label(dlg.body, text=detail, bg=theme.BASE, fg=theme.TEXT_DIM,
                 font=self.fonts.get("body"), justify="left", anchor="nw",
                 wraplength=360, padx=10, pady=8).pack(
            fill=tk.X, padx=14)
        tk.Label(dlg.body,
                 text="Windows 下 WASAPI 共享模式锁死设备混音格式，"
                      "44.1kHz 设备请改用 MME 接口或在系统声音面板固定 48kHz。",
                 bg=theme.WINDOW, fg=theme.TEXT_FAINT,
                 font=self.fonts.get("small"), justify="left",
                 wraplength=360, anchor="w").pack(
            fill=tk.X, padx=14, pady=6)

    def _set_running_ui(self, running):
        bg = theme.STOP_BG if running else theme.START_BG
        text = "停止音频处理" if running else "启动音频处理"
        self.btn_start.set_bg(bg)
        self.btn_start.configure(text=text)

    # ── 设置菜单 ──
    def _cfg_get(self, key, default):
        return self.config.get(key, default) if self.config else default

    def _cfg_set(self, key, value):
        if self.config:
            self.config.set(key, value)
            self.config.save_config()

    def _gear_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=theme.BUTTON, fg=theme.TEXT,
                    activebackground=theme.DARK, activeforeground=theme.TEXT,
                    bd=0, font=self.fonts["body"])
        m.add_command(label="系统声音", command=self._open_sound_panel)
        if not sys.platform.startswith("win"):
            m.add_command(label="虚拟声卡", command=self._open_virtual_mic)
        m.add_command(label="关于", command=self._show_about)
        m.add_separator()
        m.add_checkbutton(label="快捷键 (右Alt+>)",
                          onvalue=True, offvalue=False,
                          variable=self._hotkey_var,
                          command=lambda: self._cfg_set(
                              "hotkey_enabled", bool(self._hotkey_var.get())))
        m.add_checkbutton(label="启动时自动运行",
                          onvalue=True, offvalue=False,
                          variable=self._autorun_var,
                          command=lambda: self._cfg_set(
                              "auto_start", bool(self._autorun_var.get())))
        m.add_checkbutton(label="开机自启",
                          onvalue=True, offvalue=False,
                          variable=self._boot_var,
                          command=self._toggle_boot)
        m.tk_popup(self.btn_gear.winfo_rootx(),
                   self.btn_gear.winfo_rooty() + self.btn_gear.winfo_height())

    def _open_sound_panel(self):
        try:
            from pvplatform.system import open_sound_panel
            open_sound_panel(Logger())
        except Exception:
            pass

    def _open_virtual_mic(self):
        try:
            from pvplatform.system import ensure_virtual_mic, remove_virtual_mic, virtual_mic_ready
            ready = virtual_mic_ready()
            if ready:
                remove_virtual_mic(Logger())
            else:
                ensure_virtual_mic(Logger())
            from tkinter import messagebox
            messagebox.showinfo("虚拟声卡", "已创建，请重启音频处理生效。" if not ready
                                else "已清理。")
        except Exception as e:
            from tkinter import messagebox
            messagebox.showwarning("虚拟声卡", str(e))

    def _show_about(self):
        from .dialogs import show_about_dialog
        show_about_dialog(self.root, sizes=self.sizes, fonts=self.fonts)

    def _open_eq_editor(self):
        from .dialogs import open_eq_editor
        cur = list(self._cfg_get("eq_current_gains", [0.0] * 61) or [0.0] * 61)
        if len(cur) != 61:
            cur = [0.0] * 61

        def set_gains(g):
            self._cfg_set("eq_current_gains", list(g))
            proc = self.engine.processor
            if proc:
                try:
                    proc.set_eq_gains(list(g))
                except Exception:
                    pass

        open_eq_editor(self.root, lambda: cur, set_gains,
                       sizes=self.sizes, fonts=self.fonts)

    def _open_tse_dialog(self):
        from .dialogs import open_tse_dialog
        open_tse_dialog(self.root, self.engine, self.config,
                        sizes=self.sizes, fonts=self.fonts)

    # ── 全局热键（右 Alt + >）：独立消息窗线程 → 动作队列 ──
    def _setup_hotkey(self):
        if not sys.platform.startswith("win"):
            return
        import threading

        def work():
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            MOD_ALT, MOD_NOREPEAT = 0x0001, 0x4000
            VK_PERIOD = 0xBE
            WM_HOTKEY = 0x0312
            hk_id = 9998
            if not user32.RegisterHotKey(None, hk_id, MOD_ALT | MOD_NOREPEAT,
                                         VK_PERIOD):
                return
            # GetMessage 循环（热键消息投递到线程消息队列，无需窗口）
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == hk_id:
                    self._tray_actions.append("toggle")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        threading.Thread(target=work, daemon=True).start()

    def _toggle_boot(self):
        val = bool(self._boot_var.get())
        self._cfg_set("registry_auto_start", val)
        try:
            from pvplatform.system import enable_autostart, disable_autostart
            (enable_autostart if val else disable_autostart)(Logger())
        except Exception:
            pass

    def _add_menu(self):
        from pvengine.plugins import get_spec, all_specs
        m = tk.Menu(self.root, tearoff=0, bg=theme.BUTTON, fg=theme.TEXT,
                    activebackground=theme.DARK, activeforeground=theme.TEXT,
                    bd=0, font=self.fonts["body"])
        # 设备组：三类输入/输出 + 虚拟输入设备（自动锁定 CABLE 端点）
        dev = tk.Menu(m, tearoff=0, bg=theme.BUTTON, fg=theme.TEXT,
                      activebackground=theme.DARK,
                      activeforeground=theme.TEXT, bd=0,
                      font=self.fonts["body"])
        dev.add_command(label="本地输入设备",
                        command=lambda: self.add_spec(get_spec("audio_input")))
        dev.add_command(label="虚拟输入设备",
                        command=self.add_virtual_input)
        dev.add_command(label="网络输入设备",
                        command=lambda: self.add_spec(get_spec("remote_mic")))
        dev.add_command(label="本地输出设备",
                        command=lambda: self.add_spec(get_spec("audio_output")))
        dev.add_command(label="虚拟输出设备",
                        command=lambda: self.add_spec(get_spec("virtual_output")))
        m.add_cascade(label="设备", menu=dev)
        # 处理 / 可视化
        for kind in ("fx", "viz"):
            m.add_cascade(label=KIND_LABELS[kind],
                          menu=self._kind_menu(m, kind, all_specs()))
        m.tk_popup(self.btn_add.winfo_rootx(),
                   self.btn_add.winfo_rooty() + self.btn_add.winfo_height())

    def _kind_menu(self, parent, kind, specs):
        m = tk.Menu(parent, tearoff=0, bg=theme.BUTTON, fg=theme.TEXT,
                    activebackground=theme.DARK, activeforeground=theme.TEXT,
                    bd=0, font=self.fonts["body"])
        for sp in specs:
            if sp.kind == kind:
                m.add_command(label=sp.label,
                              command=lambda s=sp: self.add_spec(s))
        return m

    # ── 行管理 ──
    def add_spec(self, spec):
        params = {k: pdef[3] for k, pdef in (spec.params or {}).items()}
        self._make_row({"type": spec.name, "enabled": True, "params": params},
                       spec)
        self._apply_chain_change()

    def add_virtual_input(self):
        """虚拟输入设备：audio_input 行 + 自动锁定第一个 CABLE 输入端点。"""
        from pvengine.plugins import get_spec
        self.add_spec(get_spec("audio_input"))
        row = self.rows[-1]
        row._prefer_virtual = True
        self.refresh_devices()

    def _make_row(self, cfg, spec):
        row = NodeRow(self.panel, cfg, spec, self.sizes, self.fonts,
                      on_remove=lambda: self.remove_row(row),
                      on_drag_preview=self._move_row_live,
                      on_drag_commit=self._apply_chain_change,
                      on_param=lambda r, k, v: self._on_param(r, k, v))
        row.set_toggle_cb(self._apply_chain_change)
        row._on_param_cb = self._apply_chain_change
        row._apply_enabled_look()
        # viz 行：内嵌实时控件
        if spec.kind == "viz" and bool(cfg.get("enabled", True)):
            self._attach_viz(row, spec.name)
        # eq 行：展开区提供曲线编辑入口
        if spec.name == "eq":
            eb = tk.Label(row.body_frame,
                          text="打开均衡器编辑…", bg=theme.BASE,
                          fg=theme.ACCENT, cursor="hand2",
                          font=self.fonts.get("body"))
            eb.pack(anchor="w", padx=self.sizes["pad_lg"],
                    pady=self.sizes["pad_sm"])
            eb.bind("<Button-1>", lambda e: self._open_eq_editor())
        # tse 行：展开区提供参考录音入口
        if spec.name == "tse":
            tb = tk.Label(row.body_frame,
                          text="参考音频录制…", bg=theme.BASE,
                          fg=theme.ACCENT, cursor="hand2",
                          font=self.fonts.get("body"))
            tb.pack(anchor="w", padx=self.sizes["pad_lg"],
                    pady=self.sizes["pad_sm"])
            tb.bind("<Button-1>",
                    lambda e: self._open_tse_dialog())
        # 虚拟输出设备行：内嵌 VB-CABLE 驱动状态卡（原检测面板内容）
        if spec.name == "virtual_output":
            self._attach_vb_card(row)
        # 全部行内内容就绪后统一显示参数区（无展开收起）
        row.ensure_body()
        self._pack_row(row)
        self.rows.append(row)

    def _attach_viz(self, row, name):
        if name == "vu_meter":
            w = VUCanvas(row.body_frame, sizes=self.sizes)
            w.pack(fill=tk.X, pady=self.sizes["pad_sm"])
        elif name == "spectrum":
            w = SpectrumCanvas(row.body_frame, sizes=self.sizes)
            w.pack(fill=tk.X, pady=self.sizes["pad_sm"])
        else:
            return
        # 参数区由 _make_row 末尾 ensure_body 统一显示
        row.title_lbl.unbind("<Double-Button-1>")
        self._viz_widgets.append((row, name, w))

    def _viz_tick(self):
        """33ms 定时：从音频线程拉峰值/频谱喂给 viz 控件。"""
        th = self.engine.thread
        now = time.time()
        for row, name, w in self._viz_widgets:
            if not row.winfo_exists():
                continue
            if th is None or not self.engine.running:
                if name == "vu_meter":
                    w.update_level(0.0, now)
                continue
            try:
                if name == "vu_meter":
                    peak = getattr(th, "_vu_peak", 0.0)
                    w.update_level(peak, now)
                elif name == "spectrum":
                    in_buf = getattr(th, "_spectrum_in", None)
                    out_buf = getattr(th, "_spectrum_out", None)
                    in_data = out_data = None
                    if in_buf and in_buf.available() > 0:
                        n = min(2048, in_buf.available())
                        in_data = in_buf.read_latest(n)
                    if out_buf and out_buf.available() > 0:
                        n = min(2048, out_buf.available())
                        out_data = out_buf.read_latest(n)
                    if isinstance(in_data, list) and not in_data:
                        in_data = None
                    if isinstance(out_data, list) and not out_data:
                        out_data = None
                    if in_data or out_data:
                        w.update_spectrum(in_data, out_data)
            except Exception:
                pass
        self.root.after(33, self._viz_tick)

    def _attach_vb_card(self, row):
        """VB-CABLE 驱动状态卡：状态灯 + 说明 + 下载/教程（原面板内联化）。"""
        from .dialogs import vbcable_installed
        installed = vbcable_installed()
        color = "#3aa76d" if installed else "#d9534f"
        card = tk.Frame(row.body_frame, bg=theme.ALT_BASE)
        card.pack(fill=tk.X, padx=self.sizes["pad_sm"],
                  pady=(0, self.sizes["pad_sm"]))
        dot = tk.Canvas(card, bg=theme.ALT_BASE, width=12, height=12,
                        highlightthickness=0)
        dot.pack(side=tk.LEFT, padx=(2, 6), pady=4)
        dot.create_oval(1, 1, 11, 11, fill=color, outline="")
        tip = ("驱动已安装，下拉选择 CABLE Input 即可。" if installed
               else "未检测到 VB-CABLE 驱动——安装后重启 PureVox。")
        tk.Label(card, text=tip, bg=theme.ALT_BASE, fg=theme.TEXT_DIM,
                 font=self.fonts.get("small")).pack(side=tk.LEFT)
        acts = []
        if not installed:
            acts.append(("下载驱动", lambda: webbrowser_open(
                "https://download.vb-audio.com/Download_CABLE/"
                "VBCABLE_Driver_Pack45.zip")))
            acts.append(("教程", lambda: webbrowser_open(
                "https://www.bilibili.com/video/BV1i2bazGEKe/")))
        for text, cmd in acts:
            b = tk.Label(card, text=text, bg=theme.BUTTON, fg=theme.ACCENT,
                         font=self.fonts.get("small"), padx=6, pady=1,
                         cursor="hand2")
            b.pack(side=tk.RIGHT, padx=2)
            b.bind("<Button-1>", lambda e, c=cmd: c())

    def refresh_devices(self):
        """后台枚举设备，回主线程刷新各设备下拉。"""
        import threading
        def _work():
            try:
                devs = enum_io_devices()
            except Exception:
                return
            self.root.after(0, lambda: [
                r.set_devices(devs) for r in self.rows])
        threading.Thread(target=_work, daemon=True).start()

    def _on_param(self, row, key, value):
        row.cfg.setdefault("params", {})[key] = value
        # 实时生效：不重启链，直接推给运行中的处理器
        self.engine.set_live_param(self.rows.index(row), key, value)
        self._persist()

    def _pack_row(self, row):
        row.pack(fill=tk.X, pady=2)

    def _move_row_live(self, row, target):
        """拖动中实时换位：只重排显示，不持久化不重启（避免抖动）。"""
        if row is target or row not in self.rows or target not in self.rows:
            return
        self.rows.insert(self.rows.index(target),
                         self.rows.pop(self.rows.index(row)))
        for r in list(self.panel.body.winfo_children()):
            if isinstance(r, NodeRow):
                r.pack_forget()
        for r in self.rows:
            self._pack_row(r)

    def remove_row(self, row):
        row.destroy()
        if row in self.rows:
            self.rows.remove(row)
        self._apply_chain_change()

    def clear_rows(self):
        for r in list(self.rows):
            r.destroy()
        self.rows.clear()

    def _wheel(self, e):
        d = sys_wheel_delta(e)
        if not d:
            return
        # 仅当指针悬停在节点面板上才滚动
        w = self.root.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            if w is self.panel.canvas or w is self.panel.body:
                # 内容不满一屏时禁止滚动（杜绝滚出下方空白）
                if (self.panel.body.winfo_reqheight()
                        > self.panel.canvas.winfo_height()):
                    self.panel.canvas.yview_scroll(-d, "units")
                    self.panel.canvas.yview_pickplace("")
                return
            w = getattr(w, "master", None)

    def run(self):
        chain = []
        if self.config:
            try:
                chain = list(self.config.get("plugin_chain", []))
            except Exception:
                chain = []
        self.load_chain(chain)
        self.refresh_devices()
        self.root.mainloop()


def sys_wheel_delta(e):
    import sys as _sys
    if _sys.platform.startswith("win"):
        return int(e.delta / 120)
    if getattr(e, "num", 0) == 4:
        return -1
    if getattr(e, "num", 0) == 5:
        return 1
    return 0


if __name__ == "__main__":
    theme.refresh_accent()
    MainWindowTk().run()
