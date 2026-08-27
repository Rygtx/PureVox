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

"""uitk 托盘图标（仅 Windows）：ctypes Shell_NotifyIcon，零新依赖。

独立消息窗口线程；左键切换主窗显隐，右键菜单（打开/退出）。
非 Windows / 图标添加失败时 create_tray() 返回 None。

健壮性三条原则（无看门狗、无定时器、无延迟重试）：
1. 创建即校验——窗口与 NIM_ADD 在托盘线程完成后经 Event 同步回传布尔结果，
   构造函数返回前真相已知（异常路径同样置位，不死锁）；
2. 事件驱动保活——监听系统广播 TaskbarCreated（explorer 重启即收到），
   收到立刻重新 NIM_ADD，图标自愈；
3. 策略跟随状态——alive 只反映最近一次添加的真实结果，
   调用方据此决定「关窗=隐藏」还是「关窗=退出」，僵尸进程结构上不可能。
"""

import threading

WM_APP_TRAY = 0x8000 + 100
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
CMD_OPEN = 2001
CMD_QUIT = 2002


class TrayIcon:
    def __init__(self, ico_path: str, tip: str,
                 on_toggle=None, on_quit=None):
        import ctypes
        import threading
        self._ctypes = ctypes
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self._hicon = self._load_icon(ico_path)
        self._tip = tip[:127]
        self._nid = None
        # alive = 最近一次 NIM_ADD 的真实结果；创建结果在构造返回前同步可得
        self.alive = False
        self._ready = threading.Event()
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        self._ready.wait()

    # ── win32 基础 ──
    def _load_icon(self, path):
        import ctypes
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
        return ctypes.windll.user32.LoadImageW(
            None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)

    def _run(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        try:
            self._run_inner(user32, shell32, wintypes)
        except Exception:
            pass
        finally:
            # 异常路径兜底置位，保证构造函数不死锁；
            # 正常路径的置位在 NIM_ADD 完成处（早于消息循环）
            self._ready.set()

    def _run_inner(self, user32, shell32, wintypes):
        import ctypes
        # 显式签名：WPARAM/LPARAM 是指针宽整数，缺省按 int32 处理会溢出
        user32.DefWindowProcW.restype = ctypes.c_longlong
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_longlong]
        user32.PostMessageW.argtypes = [
            wintypes.HWND, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_longlong]

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", ctypes.c_uint),
                        ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            class _union(ctypes.Union):
                _fields_ = [("uTimeout", ctypes.c_uint),
                            ("uVersion", ctypes.c_uint)]
            _fields_ = [("cbSize", ctypes.c_uint),
                        ("hWnd", wintypes.HWND),
                        ("uID", ctypes.c_uint),
                        ("uFlags", ctypes.c_uint),
                        ("uCallbackMessage", ctypes.c_uint),
                        ("hIcon", wintypes.HICON),
                        ("szTip", ctypes.c_wchar * 128),
                        ("dwState", ctypes.c_uint),
                        ("dwStateMask", ctypes.c_uint),
                        ("szInfo", ctypes.c_wchar * 256),
                        ("uVersionOrTimeout", _union),
                        ("szInfoTitle", ctypes.c_wchar * 64),
                        ("dwInfoFlags", ctypes.c_uint)]

        # 系统注册广播：explorer（任务栏）重启时所有托盘图标被清空，
        # 广播此消息通知重加；这是事件驱动自愈的唯一通路，无定时器
        RegisterWindowMessageW = user32.RegisterWindowMessageW
        RegisterWindowMessageW.restype = ctypes.c_uint
        RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        wm_taskbar = RegisterWindowMessageW("TaskbarCreated")

        # 事件识别兼容两代 Shell 约定：经典版 lParam 本体即鼠标事件；
        # NOTIFYICON_VERSION_4 起 lParam 低 16 位才是事件（高位为坐标），
        # 另有键盘唤起的 WM_CONTEXTMENU / NIN_SELECT。掩码取低字全覆盖。
        WM_CONTEXTMENU = 0x007B
        NIN_SELECT = 0x0400

        def wnd_proc(hwnd, msg, wp, lp):
            if msg == WM_APP_TRAY:
                ev = lp & 0xFFFF
                if ev in (WM_LBUTTONUP, NIN_SELECT) and self.on_toggle:
                    self.on_toggle()
                elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup_menu(hwnd)
                return 0
            if msg == wm_taskbar:
                # explorer 重启广播：立刻重加图标（事件驱动自愈，无轮询）
                self.alive = bool(shell32.Shell_NotifyIconW(0x00, ctypes.byref(nid)))
                return 0
            if msg == WM_COMMAND:
                cmd = wp & 0xFFFF
                if cmd == CMD_OPEN and self.on_toggle:
                    self.on_toggle()
                elif cmd == CMD_QUIT:
                    if self.on_quit:
                        self.on_quit()
                    else:
                        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        proc = WNDPROC(wnd_proc)
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = proc
        cls.lpszClassName = "PureVoxTkTray"
        cls.hInstance = hinst
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "PureVoxTray",
                                      0, 0, 0, 0, 0, None, None, hinst, None)
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = 0x1 | 0x2 | 0x4   # MESSAGE | ICON | TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tip
        self._nid = nid
        # 创建即校验：结果直接写回 self.alive，构造函数经 Event 同步读取；
        # 不重试不等待——失败即向上层如实报告（上层据此走无托盘路径）
        self.alive = bool(shell32.Shell_NotifyIconW(0x00, ctypes.byref(nid)))  # NIM_ADD
        # 设置完成：真相已定，放行构造函数；本线程转入消息循环长期驻留
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def remove(self):
        """删除托盘图标（退出时调用；线程安全由 Shell 决定，尽力而为）。"""
        self.alive = False
        try:
            if self._nid is not None:
                ctypes.windll.shell32.Shell_NotifyIconW(
                    0x02, self._nid)   # NIM_DELETE
        except Exception:
            pass

    def _popup_menu(self, hwnd):
        import ctypes
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        MF_STRING = 0x0
        user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, "打开 PureVox")
        user32.AppendMenuW(menu, MF_STRING, CMD_QUIT, "退出")
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        TPM_RIGHTBUTTON = 0x2
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y,
                              0, hwnd, None)
        user32.DestroyMenu(menu)


def create_tray(icon_on: str, icon_off: str, on_toggle=None, on_quit=None):
    """平台入口：非 Windows / 图标缺失 / NIM_ADD 失败均返回 None。
    返回非 None 即图标已在任务栏真实存在（alive=True）；
    之后存活态经 TaskbarCreated 事件自愈，调用方关闭策略读 .alive。"""
    import os
    import sys
    if not sys.platform.startswith("win"):
        return None
    ico = icon_on if os.path.exists(icon_on) else (
        icon_off if os.path.exists(icon_off) else "")
    if not ico:
        return None
    try:
        obj = TrayIcon(ico, "PureVox", on_toggle=on_toggle, on_quit=on_quit)
    except Exception:
        return None
    return obj if obj.alive else None
