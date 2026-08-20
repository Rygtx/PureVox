# PureVox Lite Denoise Only — Tk UI 黑底白字 纯tk无ttk
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later

import tkinter as tk
from tkinter import messagebox
import subprocess
import sys
import os

# 星露谷物语像素风配色
BG = "#FFF8E1"          # 羊皮纸底
PANEL_BG = "#FFECB3"    # 面板
FG = "#5D4037"          # 深棕文字
BTN_BG = "#FFB74D"      # 南瓜橙按钮
BTN_BG2 = "#81C784"     # 田野绿按钮
ENTRY_BG = "#FFFFFF"    # 输入白底
BORDER = "#8D6E63"      # 木纹边框
HOVER_BG = "#FFE0B2"    # 悬停浅橙
SELECT_BG = "#FFCC80"   # 选中橙
TITLE_BG = "#6D4C41"    # 标题深棕
TITLE_FG = "#FFF8E1"

# 像素风格中英文免费开源字体（Ark Pixel 12px Mono，自带，不依赖系统）
PIXEL_FONTS = ["Ark Pixel 12px Mono zh_cn", "Ark Pixel 12px Mono", "Microsoft YaHei"]
PIXEL_FONT = "Microsoft YaHei"

def _get_system_dpi():
    # 已彻底甩开系统 DPI，保留接口兼容但恒返回 96
    return 96

def _enable_hidpi(root):
    # 声明 PerMonitor DPI Aware 避免系统位图拉伸发糊，缩放完全自管
    try:
        if sys.platform.startswith("win"):
            import ctypes
            # 必须在创建窗口前声明，否则已糊；此处尽早声明，失败回退
            try:
                # 2 = Per Monitor DPI Aware (Win 8.1+)，最清晰
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    # Win10 1703+ PerMonitorV2
                    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                except Exception:
                    try:
                        ctypes.windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
            # 固定 tk scaling 为 1.0 基准，后续按 zoom*0.88 自管
            try:
                root.tk.call("tk", "scaling", 1.0)
            except Exception:
                pass
    except Exception:
        pass
    return 96

def _load_pixel_font():
    try:
        if not sys.platform.startswith("win"):
            return
        import ctypes
        font_dir = os.path.join(os.path.dirname(__file__), "fonts")
        if not os.path.isdir(font_dir):
            return
        for fn in os.listdir(font_dir):
            if fn.lower().endswith((".ttf", ".otf")):
                path = os.path.abspath(os.path.join(font_dir, fn))
                ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0)
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
    except Exception:
        pass

def _pick_pixel_font(root):
    global PIXEL_FONT
    _load_pixel_font()
    try:
        avail = set(root.tk.call("font", "families"))
    except Exception:
        avail = set()
    for name in PIXEL_FONTS:
        if name in avail:
            PIXEL_FONT = name
            return name
    PIXEL_FONT = "Microsoft YaHei"
    return PIXEL_FONT

def _apply_pixel_font(root):
    name = _pick_pixel_font(root)
    try:
        import tkinter.font as tkfont
        # 使用 Font 对象避免空格解析错误，优雅回退
        for fname in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont"):
            try:
                tkfont.nametofont(fname).configure(family=name, size=10)
            except Exception:
                try:
                    tkfont.Font(root=root, name=fname, exists=True).configure(family=name, size=10)
                except Exception:
                    pass
    except Exception:
        pass
    return name
    # 强制尝试直接创建字体测试是否可用
    for cand in PIXEL_FONTS:
        try:
            f = tk.font.Font(root=root, family=cand, size=9)
            # 若创建未抛异常且实际 family 匹配，视为可用
            if f.actual("family") == cand or cand in f.actual("family"):
                PIXEL_FONT = cand
                return cand
        except Exception:
            pass
    PIXEL_FONT = "Microsoft YaHei"
    return PIXEL_FONT

class BlackCombo(tk.Frame):
    """星露谷像素风下拉，单行，过滤空行避免计算错位"""
    def __init__(self, parent, values, var, on_change, width=38, props_map=None):
        super().__init__(parent, bg=BG)
        self.var = var
        # 过滤空字符串/空白，避免输入空行导致显示空白与索引错位
        self.values = [v for v in list(values) if v and str(v).strip()]
        self.props_map = props_map or {}
        self.on_change = on_change
        self._popup = None
        self.configure(bg=BORDER, bd=0, padx=2, pady=2)
        inner = tk.Frame(self, bg=ENTRY_BG)
        inner.pack(fill=tk.BOTH, expand=True)
        self._display = tk.StringVar()
        self.var.trace_add("write", lambda *a: self._sync_display())
        self._sync_display()
        # 显示区按像素自适应，已放宽省略
        self.btn = tk.Label(inner, textvariable=self._display, bg=ENTRY_BG, fg=FG, anchor="w", padx=6, pady=6, font=(PIXEL_FONT, 11))
        self.btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.arrow = tk.Label(inner, text="▾", bg=BTN_BG, fg=TITLE_BG, font=(PIXEL_FONT, 11, "bold"), width=2, anchor="center", padx=0, pady=4, bd=1, relief=tk.RAISED)
        self.arrow.pack(side=tk.RIGHT, fill=tk.Y)
        inner.pack_propagate(False)
        inner.configure(height=28)
        for w in (self, inner, self.btn, self.arrow):
            w.bind("<Button-1>", lambda e: self._toggle())
        if var.get() not in self.values and self.values:
            var.set(self.values[0])

    def _sync_display(self, *a):
        v = self.var.get() or ""
        # 放宽省略至 50 字符，配合宽度自适应基本可显示全名
        if len(v) > 50:
            v = v[:48] + "…"
        self._display.set(v)

    def _toggle(self):
        if self._popup and self._popup.winfo_exists():
            self._close()
        else:
            self._open()

    def set_values(self, values, props_map=None):
        self.values = [v for v in list(values) if v and str(v).strip()]
        if props_map is not None:
            self.props_map = props_map
        # 变量为空或不在列表时回退首项，避免空行选中
        if (not self.var.get() or self.var.get().strip() == "" or self.var.get() not in self.values) and self.values:
            self.var.set(self.values[0])

    def _open(self):
        if not self.values or (self._popup and self._popup.winfo_exists()):
            return
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        pw = max(self.winfo_width(), 260)
        # 测量最长项，自动扩宽，放宽至 560 以显示全名
        try:
            f = tkfont.Font(family=PIXEL_FONT, size=11)
            max_w = max(f.measure(v) for v in self.values) + 40
            pw = max(pw, min(max_w, 560))
        except Exception:
            pass
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=BORDER)
        self._popup.attributes("-topmost", True)
        outer = tk.Frame(self._popup, bg=BORDER, bd=1)
        outer.pack(fill=tk.BOTH, expand=True)
        # 滚轮一格一设备，进度全局按设备数（项高28+2*1=30）
        canvas = tk.Canvas(outer, bg=ENTRY_BG, bd=0, highlightthickness=0, yscrollincrement=30)
        bar = tk.Frame(outer, bg=PANEL_BG, width=10, bd=1, relief=tk.FLAT, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(side=tk.RIGHT, fill=tk.Y, padx=(1, 0))
        thumb = tk.Frame(bar, bg=BORDER, bd=0)
        thumb.place(relx=0, rely=0, relwidth=1, height=24)
        canvas.configure(yscrollcommand=lambda *a: _update_thumb(*a))
        # 标准进度：thumb 高= h*可见/总数，y= h*first
        def _update_thumb(first, last):
            try:
                h = bar.winfo_height() or outer.winfo_height() or 180
                th = max(16, int(h * (float(last) - float(first))))
                y0 = int(h * float(first))
                # 保持在边界内
                th = min(th, h)
                y0 = max(0, min(y0, h - th))
                thumb.place_configure(height=th, y=y0)
            except Exception:
                pass
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas, bg=ENTRY_BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _sync_w(event=None):
            try:
                canvas.itemconfig(win_id, width=canvas.winfo_width())
            except Exception:
                pass
        canvas.bind("<Configure>", lambda e: _sync_w())
        # 单行显示，已去掉参数第二行，仅展示设备名
        for idx, disp in enumerate(self.values):
            is_sel = (disp == self.var.get())
            bgc = SELECT_BG if is_sel else ENTRY_BG
            item = tk.Frame(inner, bg=bgc, bd=0, height=28)
            item.pack(fill=tk.X, padx=1, pady=1)
            item.pack_propagate(False)
            l1 = tk.Label(item, text=disp, bg=bgc, fg=FG, anchor="w", font=(PIXEL_FONT, 11))
            l1.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
            for w in (item, l1):
                w.bind("<Button-1>", lambda e, i=idx: self._pick(i))
                w.bind("<Enter>", lambda e, f=item: self._hover(f, True))
                w.bind("<Leave>", lambda e, f=item, sel=is_sel: self._hover(f, False, sel))
        inner.update_idletasks()
        h = min(len(self.values), 6) * 30
        canvas.configure(height=h)
        # 外层 bd=1，需补 2px 边框
        self._popup.geometry(f"{pw}x{h+2}+{x}+{y}")
        canvas.configure(scrollregion=canvas.bbox("all"))
        _update_thumb("0", "1")
        # 初始滚动到选中项，确保可见且不留空底
        try:
            idx = self.values.index(self.var.get())
            n = len(self.values)
            vis = 6
            # 选中项尽量居中，尾部时贴底避免空行
            top = max(0, min(idx, n - vis)) / max(1, n) if n > vis else 0
            canvas.yview_moveto(top)
        except Exception:
            pass
        # 滚轮一格一设备
        def _wheel(e):
            if sys.platform.startswith("win"):
                delta = int(-1 * (e.delta / 120))
            elif getattr(e, "num", 0) == 4:
                delta = -1
            elif getattr(e, "num", 0) == 5:
                delta = 1
            else:
                delta = 0
            canvas.yview_scroll(delta, "units")
            _update_thumb(*canvas.yview())
            return "break"
        for w in (canvas, inner, outer, self._popup, bar, thumb):
            w.bind("<MouseWheel>", _wheel)
            w.bind("<Button-4>", _wheel)
            w.bind("<Button-5>", _wheel)
        def _bar_click(e):
            try:
                h = bar.winfo_height()
                th = thumb.winfo_height()
                y0 = max(0, min(e.y - th//2, h - th))
                frac = y0 / max(1, h - th)
                # 转换为选中索引
                n = len(self.values)
                idx = int(frac * (n - 1) + 0.5)
                canvas.yview_moveto(idx / max(1, n))
                _update_thumb(*canvas.yview())
            except Exception:
                pass
        bar.bind("<Button-1>", _bar_click)
        thumb.bind("<B1-Motion>", lambda e: _bar_click(e))
        self._root_bind = self.winfo_toplevel().bind("<Button-1>", self._on_root, add="+")
        self._popup.bind("<Escape>", lambda e: self._close())
        self._popup.bind("<FocusOut>", lambda e: self.after(80, self._close))
        self._popup.focus_set()

    def _hover(self, frame, enter, sel=False):
        try:
            bgc = HOVER_BG if enter else (SELECT_BG if sel else ENTRY_BG)
            frame.configure(bg=bgc)
            for c in frame.winfo_children():
                c.configure(bg=bgc)
        except Exception:
            pass

    def _on_root(self, e):
        if not self._popup or not self._popup.winfo_exists():
            return
        try:
            px, py = self._popup.winfo_rootx(), self._popup.winfo_rooty()
            pw, ph = self._popup.winfo_width(), self._popup.winfo_height()
            if px <= e.x_root <= px+pw and py <= e.y_root <= py+ph:
                return
            sx, sy = self.winfo_rootx(), self.winfo_rooty()
            sw, sh = self.winfo_width(), self.winfo_height()
            if sx <= e.x_root <= sx+sw and sy <= e.y_root <= sy+sh:
                return
        except Exception:
            pass
        self._close()

    def _pick(self, idx):
        if 0 <= idx < len(self.values):
            self.var.set(self.values[idx])
            if self.on_change:
                self.on_change()
        self._close()

    def _close(self):
        try:
            if hasattr(self, "_root_bind"):
                self.winfo_toplevel().unbind("<Button-1>", self._root_bind)
        except Exception:
            pass
        if self._popup and self._popup.winfo_exists():
            try:
                self._popup.destroy()
            except Exception:
                pass
        self._popup = None


class PixelCheck(tk.Frame):
    """星露谷像素风复选框，纯 tk，匹配黑底白字/木纹边框风格，替代系统 Checkbutton"""
    def __init__(self, parent, text, variable, command=None):
        super().__init__(parent, bg=BG)
        self.variable = variable
        self.command = command
        self._box_size = 18
        # 方框：ENTRY_BG 未选中，SELECT_BG 选中，BORDER 边框
        self.box = tk.Frame(self, bg=ENTRY_BG, width=self._box_size, height=self._box_size, bd=0, highlightbackground=BORDER, highlightthickness=1, relief=tk.FLAT)
        self.box.pack(side=tk.LEFT)
        self.box.pack_propagate(False)
        self.mark = tk.Label(self.box, text="", bg=ENTRY_BG, fg=FG, font=(PIXEL_FONT, 11, "bold"), anchor="center")
        self.mark.pack(expand=True, fill=tk.BOTH)
        self.label = tk.Label(self, text=text, bg=BG, fg=FG, font=(PIXEL_FONT, 13))
        self.label.pack(side=tk.LEFT, padx=6)
        self._sync()
        try:
            variable.trace_add("write", lambda *a: self._sync())
        except Exception:
            pass
        for w in (self, self.box, self.mark, self.label):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.bind("<Enter>", lambda e: self._on_hover(True))
            w.bind("<Leave>", lambda e: self._on_hover(False))
        self.box.bind("<Button-1>", lambda e: self.toggle())
        self.mark.bind("<Button-1>", lambda e: self.toggle())

    def _sync(self):
        on = bool(self.variable.get())
        bg = SELECT_BG if on else ENTRY_BG
        try:
            self.box.configure(bg=bg)
            self.mark.configure(bg=bg, text="✔" if on else "")
        except Exception:
            pass

    def toggle(self):
        try:
            self.variable.set(not bool(self.variable.get()))
        except Exception:
            pass
        self._sync()
        if self.command:
            try:
                self.command()
            except Exception:
                pass

    def _on_hover(self, enter):
        if bool(self.variable.get()):
            return
        bg = HOVER_BG if enter else ENTRY_BG
        try:
            self.box.configure(bg=bg)
            self.mark.configure(bg=bg)
        except Exception:
            pass

    def set_scale(self, scale):
        try:
            sz = int(18 * scale)
            self.box.configure(width=sz, height=sz)
        except Exception:
            pass


class LiteUI:
    def __init__(self, cfg, ins, outs, on_gain, on_device, on_autostart, on_close=None, on_minimize=None):
        self.cfg = cfg
        self.on_gain = on_gain
        self.on_device = on_device
        self.on_autostart = on_autostart
        self._on_close = on_close
        self._on_minimize = on_minimize

        # 创建 Tk 前先声明，避免系统位图缩放发糊
        try:
            if sys.platform.startswith("win"):
                import ctypes
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    try:
                        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                    except Exception:
                        try:
                            ctypes.windll.user32.SetProcessDPIAware()
                        except Exception:
                            pass
        except Exception:
            pass
        self.root = tk.Tk()
        dpi = _enable_hidpi(self.root)
        self._base_w, self._base_h = 420, 240
        self._zoom = int(cfg.get("zoom", 100))
        self._dpi = dpi
        self.pixel_font = _pick_pixel_font(self.root)
        # 优雅全局字体与自定义缩放（完全自管，不跟随系统；字体普遍加大，DPI 计算调小）
        try:
            import tkinter.font as tkfont
            scale = self._zoom / 100.0
            # DPI 调小 0.88x，字体加大至 12pt
            tk_scale = scale * 0.88
            self.root.tk.call("tk", "scaling", tk_scale)
            self._zoom_w = int(self._base_w * scale)
            self._zoom_h = int(self._base_h * scale)
            # 整体字体加大：名义字体 12pt
            for fname in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont"):
                try:
                    tkfont.nametofont(fname).configure(family=self.pixel_font, size=12)
                except Exception:
                    pass
            self.root.option_add("*Font", f"{{{self.pixel_font}}} 12")
        except Exception:
            pass
        self.root.title("PureVox Lite")
        self.root.configure(bg=BG)
        # 像素 P 窗口图标，仅大写 P 带边缘，无边框背景
        try:
            from PIL import Image, ImageTk, ImageDraw
            import os as _os2
            _icon = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            _dr = ImageDraw.Draw(_icon)
            try:
                from PIL import ImageFont
                _fp = _os2.path.join(_os2.path.dirname(__file__), "fonts", "ark-pixel-12px-monospaced-zh_cn.ttf")
                if _os2.path.isfile(_fp):
                    _pf = ImageFont.truetype(_fp, 56)
                    _bbox = _dr.textbbox((0, 0), "P", font=_pf, stroke_width=3)
                    _tw = _bbox[2] - _bbox[0]
                    _th = _bbox[3] - _bbox[1]
                    _dr.text(((64 - _tw)//2, (64 - _th)//2 - 2), "P", fill="#6D4C41", font=_pf, stroke_width=3, stroke_fill="#FFB74D")
                else:
                    raise FileNotFoundError
            except Exception:
                _px, _py = 16, 8
                _s = 7
                _pat = [[1,1,1,1],[1,0,0,1],[1,0,0,1],[1,1,1,1],[1,0,0,0],[1,0,0,0],[1,0,0,0]]
                for _dr2 in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,-1),(-1,1),(1,1)]:
                    for _r, _row in enumerate(_pat):
                        for _c, _v in enumerate(_row):
                            if _v:
                                _x0 = _px + _c*_s + _dr2[0]
                                _y0 = _py + _r*_s + _dr2[1]
                                _dr.rectangle([_x0, _y0, _x0+_s-1, _y0+_s-1], fill="#FFB74D")
                for _r, _row in enumerate(_pat):
                    for _c, _v in enumerate(_row):
                        if _v:
                            _x0 = _px + _c*_s
                            _y0 = _py + _r*_s
                            _dr.rectangle([_x0, _y0, _x0+_s-1, _y0+_s-1], fill="#6D4C41")
            _photo = ImageTk.PhotoImage(_icon)
            self.root.iconphoto(False, _photo)
            self._icon_photo = _photo
        except Exception:
            pass
        self.root.overrideredirect(True)
        # 使无边框窗口仍显示在任务栏
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            exstyle = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            exstyle |= 0x00040000  # WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, exstyle)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0007)  # SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_FRAMECHANGED
        except Exception:
            pass
        self.root.resizable(False, False)
        self.root.geometry(f"{self._zoom_w}x{self._zoom_h}+600+400")
        self.root.update_idletasks()
        try:
            req_w = self.root.winfo_reqwidth()
            req_h = self.root.winfo_reqheight()
            if req_w > self._zoom_w or req_h > self._zoom_h:
                self.root.geometry(f"{max(self._zoom_w, req_w)}x{max(self._zoom_h, req_h)}+600+400")
        except Exception:
            pass

        # 自绘标题栏（可拖动）
        # 星露谷木纹标题栏
        bar = tk.Frame(self.root, bg=TITLE_BG, height=30, bd=0)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.bind("<Button-1>", self._drag_start)
        bar.bind("<B1-Motion>", self._drag_move)
        self._drag_x = 0
        self._drag_y = 0
        # 像素标题（可拖动）
        title_lbl = tk.Label(bar, text="◆ PureVox Lite", bg=TITLE_BG, fg=TITLE_FG, font=(PIXEL_FONT, 14, "bold"))
        title_lbl.pack(side=tk.LEFT, padx=10, pady=4)
        # 仅保留叉，正方形 28x28 像素风
        close_btn = tk.Button(bar, text="✕", bg="#E57373", fg="white", bd=1, relief=tk.RAISED, highlightbackground=BORDER, font=(PIXEL_FONT, 11, "bold"), width=2, height=1, padx=6, pady=2, command=self._do_close, activebackground="#EF5350")
        close_btn.pack(side=tk.RIGHT, padx=4, pady=4)
        # 标题栏整体及文字均可拖动
        for w in (bar, title_lbl):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        self.in_var = tk.StringVar(value=cfg.get("input_device", ""))
        self.out_var = tk.StringVar(value=cfg.get("output_device", ""))

        # ins/outs 可能是 (disp,idx) 或 (disp,idx,props)
        def _unpack(lst):
            names, mp, props = [], {}, {}
            for item in lst:
                if len(item) == 3:
                    n, i, p = item
                    names.append(n); mp[n] = i; props[n] = p
                else:
                    n, i = item
                    names.append(n); mp[n] = i; props[n] = ""
            return names, mp, props
        self.in_names, self.in_map, self.in_props = _unpack(ins)
        self.out_names, self.out_map, self.out_props = _unpack(outs)

        row1 = tk.Frame(body, bg=BG)
        row1.pack(fill=tk.X, pady=4)
        tk.Label(row1, text="输入", bg=BG, fg=FG, width=6, anchor="w", font=(PIXEL_FONT, 13)).pack(side=tk.LEFT)
        self.cb_in = BlackCombo(row1, self.in_names, self.in_var, self._device_changed, width=38, props_map=self.in_props)
        self.cb_in.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        row2 = tk.Frame(body, bg=BG)
        row2.pack(fill=tk.X, pady=4)
        tk.Label(row2, text="输出", bg=BG, fg=FG, width=6, anchor="w", font=(PIXEL_FONT, 13)).pack(side=tk.LEFT)
        self.cb_out = BlackCombo(row2, self.out_names, self.out_var, self._device_changed, width=38, props_map=self.out_props)
        self.cb_out.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        row3 = tk.Frame(body, bg=BG)
        row3.pack(fill=tk.X, pady=6)
        tk.Label(row3, text="前增益", bg=BG, fg=FG, width=6, anchor="w", font=(PIXEL_FONT, 13)).pack(side=tk.LEFT)
        tk.Button(row3, text="−", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, width=3, font=("Arial", 11, "bold"), activebackground=HOVER_BG, activeforeground=FG, command=lambda: self._step("pre", -1)).pack(side=tk.LEFT, padx=4)
        self.pre_var = tk.StringVar(value=str(int(cfg.get("pre_gain_db", 0))))
        self.ent_pre = tk.Entry(row3, textvariable=self.pre_var, bg=ENTRY_BG, fg=FG, insertbackground=FG, width=6, justify="center", relief=tk.FLAT, bd=1, highlightbackground=BORDER, highlightthickness=1, font=(PIXEL_FONT, 13))
        self.ent_pre.pack(side=tk.LEFT)
        self.ent_pre.bind("<Return>", lambda e: self._gain_enter("pre"))
        self.ent_pre.bind("<FocusOut>", lambda e: self._gain_enter("pre"))
        tk.Button(row3, text="+", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, width=3, font=("Arial", 11, "bold"), activebackground=HOVER_BG, activeforeground=FG, command=lambda: self._step("pre", 1)).pack(side=tk.LEFT, padx=4)
        tk.Label(row3, text="dB", bg=BG, fg=FG, font=(PIXEL_FONT, 13)).pack(side=tk.LEFT)

        row4 = tk.Frame(body, bg=BG)
        row4.pack(fill=tk.X, pady=4)
        tk.Label(row4, text="后增益", bg=BG, fg=FG, width=6, anchor="w", font=(PIXEL_FONT, 13)).pack(side=tk.LEFT)
        tk.Button(row4, text="−", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, width=3, font=("Arial", 11, "bold"), activebackground=HOVER_BG, activeforeground=FG, command=lambda: self._step("post", -1)).pack(side=tk.LEFT, padx=4)
        self.post_var = tk.StringVar(value=str(int(cfg.get("post_gain_db", 0))))
        self.ent_post = tk.Entry(row4, textvariable=self.post_var, bg=ENTRY_BG, fg=FG, insertbackground=FG, width=6, justify="center", relief=tk.FLAT, bd=1, highlightbackground=BORDER, highlightthickness=1, font=(PIXEL_FONT, 12))
        self.ent_post.pack(side=tk.LEFT)
        self.ent_post.bind("<Return>", lambda e: self._gain_enter("post"))
        self.ent_post.bind("<FocusOut>", lambda e: self._gain_enter("post"))
        tk.Button(row4, text="+", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, width=3, font=("Arial", 10, "bold"), activebackground=HOVER_BG, activeforeground=FG, command=lambda: self._step("post", 1)).pack(side=tk.LEFT, padx=4)
        tk.Label(row4, text="dB", bg=BG, fg=FG, font=(PIXEL_FONT, 12)).pack(side=tk.LEFT)

        row5 = tk.Frame(body, bg=BG)
        row5.pack(fill=tk.X, pady=8)
        self.autostart_var = tk.BooleanVar(value=bool(cfg.get("autostart", False)))
        self.cb_autostart = PixelCheck(row5, text="开机自启", variable=self.autostart_var, command=self._autostart_toggle)
        self.cb_autostart.pack(side=tk.LEFT)
        tk.Button(row5, text="声音控制面板", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, font=(PIXEL_FONT, 12), padx=8, pady=4, activebackground=HOVER_BG, activeforeground=FG, command=self._open_sound).pack(side=tk.RIGHT, padx=4)
        tk.Button(row5, text="VB 面板", bg=BTN_BG, fg=FG, bd=0, relief=tk.FLAT, font=(PIXEL_FONT, 12), padx=8, pady=4, activebackground=HOVER_BG, activeforeground=FG, command=self._open_vb).pack(side=tk.RIGHT)

        self.status = tk.Label(body, text="运行中 · 48kHz 单声道 · 降噪常驻", bg=BG, fg="#AAAAAA", font=(PIXEL_FONT, 10))
        self.status.pack(side=tk.BOTTOM, pady=4)
        # 定时校验——已 DPI Unaware，不再跟随系统 DPI 回退，只保留空轮询以兼容旧逻辑
        self.root.after(1000, self._poll_dpi)

    def set_zoom(self, percent):
        try:
            percent = max(75, min(200, int(percent)))
            self._zoom = percent
            self.cfg["zoom"] = percent
            scale = percent / 100.0
            # 先设缩放，再算几何，避免立即回弹；立即刷新内容字体以免“只放大窗口”，DPI 0.88x 调小
            try:
                self.root.tk.call("tk", "scaling", scale * 0.88)
            except Exception:
                pass
            # 立即刷新内容：名义字体重触发缩放，匿名tuple字体用新Font对象重建
            try:
                import tkinter.font as tkfont
                for fname in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont"):
                    try:
                        tkfont.nametofont(fname).configure(size=12)
                    except Exception:
                        pass
                def _refresh_fonts(w):
                    try:
                        fval = w.cget("font")
                    except Exception:
                        fval = None
                    if fval:
                        try:
                            # 命名体系已在上一步处理，跳过 Tk* 命名
                            if isinstance(fval, str) and fval.startswith("Tk"):
                                pass
                            else:
                                orig = tkfont.Font(font=w.cget("font"))
                                nf = tkfont.Font(family=orig.actual("family"), size=orig.actual("size"), weight=orig.actual("weight"), slant=orig.actual("slant"))
                                try:
                                    nf.configure(underline=orig.actual("underline"), overstrike=orig.actual("overstrike"))
                                except Exception:
                                    pass
                                w.configure(font=nf)
                        except Exception:
                            pass
                    for ch in w.winfo_children():
                        _refresh_fonts(ch)
                _refresh_fonts(self.root)
            except Exception:
                pass
            w = int(self._base_w * scale)
            h = int(self._base_h * scale)
            self._zoom_w, self._zoom_h = w, h
            self.root.update_idletasks()
            try:
                req_w = self.root.winfo_reqwidth()
                req_h = self.root.winfo_reqheight()
                w = max(w, req_w)
                h = max(h, req_h)
                self._zoom_w, self._zoom_h = w, h
            except Exception:
                pass
            # 保留窗口位置，只改变大小
            try:
                cur = self.root.geometry()
                parts = cur.split("+", 1)
                pos = "+" + parts[1] if len(parts) > 1 else ""
                self.root.geometry(f"{w}x{h}{pos}")
            except Exception:
                self.root.geometry(f"{w}x{h}")
            # 持久化
            try:
                from config import save as _save
                _save(self.cfg)
            except Exception:
                pass
        except Exception:
            pass

    def _poll_dpi(self):
        # 已完全自管 DPI，不跟随系统回退缩放；轮询只保留定时器，不改变几何
        try:
            self.root.after(1000, self._poll_dpi)
        except Exception:
            pass

    def _drag_start(self, e):
        self._drag_x = e.x
        self._drag_y = e.y

    def _drag_move(self, e):
        try:
            x = self.root.winfo_x() + e.x - self._drag_x
            y = self.root.winfo_y() + e.y - self._drag_y
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _do_close(self):
        if self._on_close:
            self._on_close()
        else:
            self.root.destroy()

    def _do_minimize(self):
        if self._on_minimize:
            self._on_minimize()
        else:
            self.root.iconify()

    def _step(self, which, delta):
        var = self.pre_var if which == "pre" else self.post_var
        try:
            v = int(float(var.get()))
        except Exception:
            v = 0
        v = max(-20, min(30, v + delta))
        var.set(str(v))
        self._gain_enter(which)

    def _gain_enter(self, which):
        try:
            v = int(float(self.pre_var.get() if which == "pre" else self.post_var.get()))
        except Exception:
            messagebox.showerror("错误", "增益必须是整数 (-20~30)")
            return
        if v < -20 or v > 30:
            messagebox.showerror("错误", "增益范围 -20~30 dB")
            return
        # 回写整数规范
        if which == "pre":
            self.pre_var.set(str(v))
        else:
            self.post_var.set(str(v))
        self.on_gain(which, v)

    def _device_changed(self):
        in_name = self.in_var.get()
        out_name = self.out_var.get()
        self.on_device(in_name, out_name)

    def _autostart_toggle(self):
        self.on_autostart(bool(self.autostart_var.get()))

    def _open_sound(self):
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["control", "mmsys.cpl"], shell=True)
            else:
                subprocess.Popen(["pavucontrol"])
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def _open_vb(self):
        try:
            candidates = [
                r"C:\Program Files\VB\CABLE\VBCABLE_ControlPanel.exe",
                r"C:\Program Files (x86)\VB\CABLE\VBCABLE_ControlPanel.exe",
            ]
            for p in candidates:
                if os.path.exists(p):
                    subprocess.Popen([p], shell=False)
                    return
            self._open_sound()
            self.root.after(500, lambda: messagebox.showinfo("VB-CABLE", "未找到 VBCABLE_ControlPanel.exe，已打开声音控制面板。\n请在播放/录制页查看 CABLE Input/Output。"))
        except Exception as e:
            messagebox.showerror("错误", str(e))

    def run(self):
        self.root.mainloop()

