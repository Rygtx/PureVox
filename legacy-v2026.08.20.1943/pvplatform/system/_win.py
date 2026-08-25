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
Windows 系统服务实现（win32 / reg / COM）。

仅在 Windows 被 platform.system 模块按需延迟导入；本文件不应对 Linux
造成 import 即崩溃（其 win32 依赖在函数内部导入）。
"""

import ctypes
import os
import sys
import threading


def acquire_single_instance_win(lock_name: str) -> bool:
    """Windows 命名 Mutex 单实例锁。返回 True 表示成功获得锁。

    锁句柄存于模块级全局，进程存活期间保持打开，退出时系统自动释放。
    """
    try:
        import win32event
        import win32api
        import winerror
    except ImportError:
        raise
    global _SINGLE_INSTANCE_MUTEX
    _SINGLE_INSTANCE_MUTEX = win32event.CreateMutex(None, True, lock_name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return False
    return True


_SINGLE_INSTANCE_MUTEX = None


def is_autostart_win() -> bool:
    """检查注册表 Run 键是否含 PureVox。"""
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(k, "PureVox")
            winreg.CloseKey(k)
            return True
        except FileNotFoundError:
            winreg.CloseKey(k)
            return False
    except Exception:
        return False


def run_as_admin_win(cmd: str, logger) -> bool:
    """通过 UAC 以管理员权限运行注册表命令（reg.exe）。"""
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", "reg.exe", cmd, None, 1) > 32
    except Exception as e:
        logger.err(f"管理员权限: {e}")
        return False


def enable_autostart_win(logger) -> bool:
    try:
        exe = os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)
        return run_as_admin_win(
            f'add "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run" '
            f'/v "PureVox" /t REG_SZ /d "\\"{exe}\\"" /f', logger)
    except Exception as e:
        logger.err(f"添加注册表: {e}")
        return False


def disable_autostart_win(logger) -> bool:
    try:
        return run_as_admin_win(
            'delete "HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run" '
            '/v "PureVox" /f', logger)
    except Exception as e:
        logger.err(f"删除注册表: {e}")
        return False


def add_firewall_rule_win(logger):
    """使用 win32com 添加进站防火墙规则。"""
    try:
        import win32com.client
        exe = os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)
        fw = win32com.client.Dispatch("HNetCfg.FwPolicy2")
        rule = win32com.client.Dispatch("HNetCfg.FwRule")
        rule.Name = "PureVox AI Mic Denoise"
        rule.Description = "PureVox AI Mic Denoise"
        rule.Direction = 1
        rule.Action = 1
        rule.Program = exe
        rule.Enabled = True
        rule.Profiles = 0x7FFFFFFF
        fw.Rules.Add(rule)
        logger.sys("防火墙规则: 已添加")
    except Exception:
        pass


def beep_win(freq_hz: int, duration_ms: int):
    threading.Thread(
        target=lambda: ctypes.windll.kernel32.Beep(int(freq_hz), int(duration_ms)),
        daemon=True).start()


def open_sound_panel_win(logger):
    try:
        import subprocess
        control = os.path.join(
            os.environ.get('SystemRoot', 'C:\\Windows'), 'System32', 'control.exe')
        subprocess.run([control, "mmsys.cpl"], check=False, shell=True)
        logger.msg("已打开声音控制面板")
    except Exception as e:
        logger.err(f"打开失败: {e}")


def open_virtual_cable_panel_win(logger):
    p = r"C:\Program Files\VB\CABLE\VBCABLE_ControlPanel.exe"
    if not os.path.exists(p):
        logger.warn("未找到 VB-CABLE")
        return
    try:
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", p, None, None, 1) > 32:
            logger.msg("已打开 VB-CABLE")
    except Exception as e:
        logger.err(f"打开失败: {e}")


def system_accent_color_win():
    """从注册表读取 DWM AccentColor。返回 (r, g, b) 三元组或 None。"""
    try:
        import winreg
        from PySide6.QtGui import QColor
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\DWM")
        val, _ = winreg.QueryValueEx(key, "AccentColor")
        winreg.CloseKey(key)
        # DWM AccentColor is ARGB: 0xAARRGGBB
        return QColor(val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)
    except Exception:
        return None


def set_titlebar_theme_win(win_id: int, dark: bool):
    """通过 DWM API 设置 Windows 标题栏深色/浅色（Win10 1809+ / Win11）。"""
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            win_id, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1 if dark else 0)),
            ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass