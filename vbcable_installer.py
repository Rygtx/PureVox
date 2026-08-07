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

# vbcable_installer.py
import ctypes
import subprocess
import os
import sys
import time
import winreg
from typing import Optional, Callable

# VB-CABLE 驱动作者不允许将其内置到其它软件安装流程，
# 故 PureVox 不在安装包内分发二进制，而是随包附带官方原版 zip 驱动包
# （tools/VBCABLE_Driver_Pack45.zip），安装时从 zip 提取 setup 再静默安装。
VBCABLE_DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"

# 用户可放入这些目录（软件根目录、tools/、CABLE/、%USERPROFILE%\Downloads）
_PLACE_SUBDIRS = ("", "tools", "CABLE")

# 官方驱动包 zip 文件名（含 VBCABLE_Setup_x64.exe）
ZIP_FILENAMES = ("VBCABLE_Driver_Pack45.zip", "VBCABLE_Driver_Pack43.zip")


def _get_resource_dir() -> str:
    """返回资源目录（兼容开发环境与打包环境）。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 会把资源解压到 _MEIPASS 临时目录
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        return os.path.dirname(os.path.abspath(__file__))


def _candidate_paths(filename: str) -> list:
    """返回候选安装包路径（优先度从高到低，均为用户手动放置位置）。"""
    base_dir = _get_resource_dir()
    paths = [os.path.join(base_dir, filename)]
    for sub in _PLACE_SUBDIRS[1:]:
        paths.append(os.path.join(base_dir, sub, filename))
    paths.append(os.path.join(os.path.expanduser("~"), "Downloads", filename))
    return paths


def _get_driver_pack_path() -> Optional[str]:
    """返回官方驱动包 zip 的路径（随 PureVox 附带的 tools/ 或用户放置处）。"""
    for filename in ZIP_FILENAMES:
        for p in _candidate_paths(filename):
            if os.path.exists(p):
                return p
    return None


def _extract_setup_from_zip(zip_path: str, dest_dir: Optional[str] = None) -> Optional[str]:
    """从官方驱动包 zip 提取 VBCABLE_Setup_x64.exe，返回其路径。"""
    import zipfile
    dest_dir = dest_dir or os.path.dirname(zip_path)
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                base = os.path.basename(name)
                if base.lower() in ("vbcable_setup_x64.exe", "vbcable_setup.exe"):
                    extracted = os.path.join(dest_dir, base)
                    with z.open(name) as src, open(extracted, "wb") as dst:
                        dst.write(src.read())
                    return extracted
    except Exception:
        return None
    return None


def _get_vbcable_setup_path() -> Optional[str]:
    """返回可用的 VBCABLE_Setup_x64.exe 路径（先找现成 exe，再从附带 zip 提取）。"""
    for p in _candidate_paths("VBCABLE_Setup_x64.exe"):
        if os.path.exists(p):
            return p
    # 附带官方 zip → 提取 setup exe
    pack = _get_driver_pack_path()
    if pack:
        return _extract_setup_from_zip(pack)
    return None


def vbcable_setup_found() -> bool:
    """用户是否已提供 VB-CABLE 安装包。"""
    return _get_vbcable_setup_path() is not None


def _get_vbcable_license_key() -> Optional[str]:
    """从注册表读取 VB-Cable 许可证密钥（若已注册过）。"""
    try:
        key_paths = [
            r"SOFTWARE\VB-Audio\VB-Cable",
            r"SOFTWARE\WOW6432Node\VB-Audio\VB-Cable",
        ]

        for key_path in key_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ) as key:
                    for value_name in ["License", "Key", "Serial", "ID", "GUID"]:
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            if value:
                                return str(value)
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                continue

        return None
    except Exception:
        return None


def _check_vbcable_installed() -> bool:
    """通过 CABLE 设备是否存在来判断 VB-Cable 是否已安装。"""
    def _check_with_pyaudio() -> bool:
        try:
            import pyaudio
            p = pyaudio.PyAudio()

            has_cable_input = False
            has_cable_output = False

            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                name = dev.get('name', '')
                if 'CABLE Output' in name or 'CABLE Input' in name:
                    if dev.get('maxInputChannels', 0) > 0 and 'CABLE Output' in name:
                        has_cable_input = True
                    if dev.get('maxOutputChannels', 0) > 0 and 'CABLE Input' in name:
                        has_cable_output = True

            p.terminate()
            return has_cable_input and has_cable_output
        except Exception:
            return False

    import threading
    result_box = [False]

    def worker():
        result_box[0] = _check_with_pyaudio()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)

    if thread.is_alive():
        pass
    else:
        if result_box[0]:
            return True

    try:
        result = subprocess.run(
            ["powershell", "-Command", "Get-PnpDevice | Where-Object { $_.FriendlyName -like '*VB-Audio Virtual Cable*' }"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return "VB-Audio" in result.stdout
    except Exception:
        return False


def install_vbcable(log_func: Optional[Callable[[str], None]] = None) -> bool:
    """检测并安装 VB-Cable 驱动（需管理员权限）。"""
    if _check_vbcable_installed():
        return True

    setup_path = _get_vbcable_setup_path()
    if not setup_path:
        if log_func:
            log_func("未找到 VBCABLE_Setup_x64.exe 或官方驱动包，请到官方下载后放入软件目录（或 tools/ 子目录）")
            log_func(f"VB-CABLE 下载地址: {VBCABLE_DOWNLOAD_URL}")
        return False

    if log_func:
        log_func(f"找到安装程序: {setup_path}")
        log_func("正在安装 VB-Cable（需要管理员权限）...")

    try:
        license_key = _get_vbcable_license_key()

        cmd_args = "-i -h"
        if license_key:
            cmd_args = f"-i -h -s -k {license_key}"

        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", setup_path, cmd_args, None, 1
        )

        if result <= 32:
            if log_func:
                log_func(f"安装程序启动失败，错误码: {result}")
            return False

        if log_func:
            log_func('请在弹出的 UAC 对话框中点击"是"以授权安装...')
            log_func("等待设备初始化...")

        max_wait = 60
        waited = 0
        installed = False

        while waited < max_wait:
            time.sleep(5)
            waited += 5

            if _check_vbcable_installed():
                installed = True
                if log_func:
                    log_func("VB-Cable 安装完成")
                break
            else:
                if log_func:
                    log_func(f"等待设备就绪... ({waited}s)")

        if not installed:
            if log_func:
                log_func("VB-Cable 安装可能未完成，请手动安装")
            return False

        return True

    except subprocess.TimeoutExpired:
        if log_func:
            log_func("VB-Cable 安装超时")
        return False
    except Exception as e:
        if log_func:
            log_func(f"VB-Cable 安装失败: {e}")
        return False
