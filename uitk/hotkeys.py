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

"""音效板全局热键（仅 Windows）：Win32 RegisterHotKey，事件驱动零轮询。

独占一条消息线程 + message-only 窗口：
- set_bindings(flags) 投递 WM_APP_REBIND，线程内先全注销再按新表注册
  （Ctrl+Alt+1..9 对应垫子序号，MOD_NOREPEAT 防连发）；
- WM_HOTKEY → on_pad(index) 回调。
非 Windows 平台 set_bindings 为空操作；注销失败静默（热键属尽力而为）。
"""

import ctypes
import threading
from ctypes import wintypes

WM_APP_REBIND = 0x8000 + 201
WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
ID_BASE = 0xB000
VK_1 = 0x31
HWND_MESSAGE = -3


class PadHotkeys:
    """音效板热键宿主。on_pad(index) 在热键线程回调（UI 侧自行投递主线程）。"""

    def __init__(self, on_pad):
        self._on_pad = on_pad
        self._flags = [False] * 9
        self._lock = threading.Lock()
        self._hwnd = None
        self._proc = None          # 保活 WNDPROC 闭包，防 GC
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait()

    def set_bindings(self, flags):
        """更新启用表（序号 0..8 → 垫子），线程内异步重注册。"""
        with self._lock:
            self._flags = ([bool(f) for f in flags] + [False] * 9)[:9]
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_APP_REBIND, 0, 0)

    def stop(self):
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # ── 热键线程 ──
    def _run(self):
        if not sys_platform_win():
            self._ready.set()
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        registered = []

        def rebind():
            for i in registered:
                user32.UnregisterHotKey(None, ID_BASE + i)
            registered.clear()
            with self._lock:
                flags = list(self._flags)
            for i, on in enumerate(flags):
                if on and user32.RegisterHotKey(
                        None, ID_BASE + i,
                        MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_1 + i):
                    registered.append(i)

        def wnd_proc(hwnd, msg, wp, lp):
            if msg == WM_HOTKEY:
                idx = wp - ID_BASE
                if 0 <= idx < 9:
                    try:
                        self._on_pad(idx)
                    except Exception:
                        pass
                return 0
            if msg == WM_APP_REBIND:
                rebind()
                return 0
            if msg == WM_CLOSE:
                rebind_clear(registered)
                user32.DestroyWindow(hwnd)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM)
        proc = WNDPROC(wnd_proc)
        self._proc = proc

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

        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = proc
        cls.lpszClassName = "PureVoxPadHotkeys"
        cls.hInstance = hinst
        user32.RegisterClassW(ctypes.byref(cls))
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        # message-only 窗口：parent = HWND_MESSAGE
        hwnd = user32.CreateWindowExW(
            0, "PureVoxPadHotkeys", "PureVoxPadHotkeys", 0,
            0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE), None, hinst, None)
        self._hwnd = hwnd
        rebind()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def rebind_clear(registered):
    user32 = ctypes.windll.user32
    for i in registered:
        try:
            user32.UnregisterHotKey(None, ID_BASE + i)
        except Exception:
            pass
    registered.clear()


def sys_platform_win() -> bool:
    import sys
    return sys.platform.startswith("win")
