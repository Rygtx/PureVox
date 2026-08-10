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

# dialog_vbcable_check.py
# VB-CABLE 由用户自行下载并安装（PureVox 不做自动安装），本模块负责检测与面板。
import subprocess
import threading
import time as _time
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

VBCABLE_DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VB_TUTORIAL_URL = "https://www.bilibili.com/video/BV1i2bazGEKe/"


def _check_with_pyaudio() -> bool:
    """通过 PyAudio（PortAudio）枚举 CABLE 输入/输出设备判断是否已安装。"""
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


def _check_vbcable_installed() -> bool:
    """通过 CABLE 设备是否存在来判断 VB-Cable 是否已安装。"""
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


def show_vbcable_dialog(config=None) -> bool:
    """VB-CABLE 检测面板（Windows）：状态指示灯 + 端点说明 + 下载引导，可勾选跳过检测。
    config: 可选 ConfigManager（写入 vbcable_check_enabled）。
    返回 True 表示检测到已安装。"""
    _GREEN = "#3aa76d"
    _RED = "#d9534f"

    installed = _check_vbcable_installed()

    dlg = QDialog(None)
    dlg.setWindowTitle("PureVox - 虚拟声卡")
    dlg.setMinimumWidth(420)
    dlg.setWindowModality(Qt.ApplicationModal)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)
    layout.setContentsMargins(16, 16, 16, 12)

    # 状态行：指示灯 + 标题
    state_row = QHBoxLayout()
    dot = QLabel()
    dot.setFixedSize(12, 12)
    dot.setStyleSheet("background:%s; border-radius:6px;" % (_GREEN if installed else _RED))
    state_row.addWidget(dot)
    title = QLabel("%s" % ("已安装" if installed else "未安装"))
    title.setStyleSheet("font-size: 11pt;")
    state_row.addWidget(title)
    state_row.addStretch()
    layout.addLayout(state_row)

    if installed:
        body_lines = [
            "• CABLE Output —— 其它应用选作「麦克风」；",
            "• CABLE Input —— PureVox 输出写入此处，供 CABLE Output 使用。",
            "远程麦克风功能将使用它把声音送到其它应用。",
        ]
    else:
        body_lines = [
            "请自行下载并安装：",
            "1. 点击\"官方驱动包\"下载 zip；",
            "2. 解压后双击运行 VBCABLE_Setup_x64.exe，按提示完成安装；",
            "3. 装好后重新打开此面板，指示灯变绿。",
        ]
    tip = QLabel("\n".join(body_lines))
    tip.setWordWrap(True)
    tip.setStyleSheet("font-size: 10pt;")
    layout.addWidget(tip)

    # 工具卡片，含驱动包下载 + 安装视频教程（未安装时显示）
    if not installed:
        card_style = (
            "QFrame { border: 1px solid palette(mid); border-radius: 4px; "
            "background: palette(base); padding: 4px 6px; }"
        )
        card_layout = QVBoxLayout()
        card_layout.setSpacing(4)
        row = QHBoxLayout()
        tag = QLabel("  VB-CABLE 驱动 ")
        tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
        row.addWidget(tag)
        row.addStretch()
        btn_dl = QPushButton("下载官方驱动包")
        btn_dl.setFixedHeight(26)
        btn_dl.setCursor(Qt.PointingHandCursor)
        btn_dl.clicked.connect(lambda _=False: QDesktopServices.openUrl(QUrl(VBCABLE_DOWNLOAD_URL)))
        row.addWidget(btn_dl)
        btn_video = QPushButton("安装视频教程")
        btn_video.setFixedHeight(26)
        btn_video.setCursor(Qt.PointingHandCursor)
        btn_video.clicked.connect(lambda _=False: QDesktopServices.openUrl(QUrl(VB_TUTORIAL_URL)))
        row.addWidget(btn_video)
        card_layout.addLayout(row)
        card_frame = QFrame()
        card_frame.setStyleSheet(card_style)
        card_frame.setLayout(card_layout)
        layout.addWidget(card_frame)

    # 检测开关（默认勾选 = 开启检测；取消勾选 = 跳过检测，不再提示）
    enabled = config.get("vbcable_check_enabled", True) if config is not None else True
    check_cb = QCheckBox("检测虚拟麦克风（默认开启）")
    check_cb.setChecked(bool(enabled))
    check_cb.setToolTip("默认检测 VB-CABLE 虚拟麦克风。如果你有自己的虚拟麦克风\n"
                        "或自定义配置，可取消勾选跳过检测——请确认你知道自己在做什么")
    check_cb.setStyleSheet("font-size: 10pt;")
    layout.addWidget(check_cb)

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_ok = QPushButton("确定")
    btn_ok.setFixedHeight(28)
    btn_ok.setFixedWidth(88)
    btn_ok.setCursor(Qt.PointingHandCursor)

    def _ok():
        if config is not None:
            config.set("vbcable_check_enabled", check_cb.isChecked())
            config.save_config()
        dlg.accept()

    btn_ok.clicked.connect(_ok)
    btn_row.addWidget(btn_ok)
    layout.addLayout(btn_row)

    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    dlg.show()
    while dlg.isVisible():
        QApplication.processEvents()
        _time.sleep(0.02)
    return installed