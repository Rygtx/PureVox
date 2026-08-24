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
非 Windows 平台 create_tray() 返回 None。
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
        self._ctypes = ctypes
        self.on_toggle = on_toggle
        self.on_quit = on_quit
        self._hicon = self._load_icon(ico_path)
        self._tip = tip[:127]
        self._nid = None
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

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

        def wnd_proc(hwnd, msg, wp, lp):
            if msg == WM_APP_TRAY:
                if lp == WM_LBUTTONUP and self.on_toggle:
                    self.on_toggle()
                elif lp == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
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
        shell32 = ctypes.windll.shell32
        shell32.Shell_NotifyIconW(0x00, ctypes.byref(nid))    # NIM_ADD
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def remove(self):
        """删除托盘图标（退出时调用；线程安全由 Shell 决定，尽力而为）。"""
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
    """平台入口：非 Windows 返回 None。图标优先用运行态（on）。"""
    import os
    import sys
    if not sys.platform.startswith("win"):
        return None
    ico = icon_on if os.path.exists(icon_on) else (
        icon_off if os.path.exists(icon_off) else "")
    if not ico:
        return None
    try:
        return TrayIcon(ico, "PureVox", on_toggle=on_toggle, on_quit=on_quit)
    except Exception:
        return None
