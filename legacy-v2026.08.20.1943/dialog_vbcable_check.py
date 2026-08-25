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
#
# 检测逻辑（配合 ui_pyside6._check_vbcable）：默认开启检测，但只有在
# VB-CABLE「未安装」时才弹出面板；已安装则无事发生。面板结构不随安装状态变化：
# 状态灯 + 双端点说明（48kHz）+ 驱动卡片（打开控制面板/下载/教程）+ 检测开关。
#
# 检测单一实现路径：只走 PyAudio 枚举双端点（CABLE Input 有输出通道 +
# CABLE Output 有输入通道），不做任何 PnP/驱动的退化回退（退化会误判禁用/残留设备）。
import threading
import time as _time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

VBCABLE_DOWNLOAD_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VB_TUTORIAL_URL = "https://www.bilibili.com/video/BV1i2bazGEKe/"

_GREEN = "#3aa76d"
_RED = "#d9534f"


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
            if 'CABLE Output' in name and dev.get('maxInputChannels', 0) > 0:
                has_cable_input = True
            if 'CABLE Input' in name and dev.get('maxOutputChannels', 0) > 0:
                has_cable_output = True

        p.terminate()
        return has_cable_input and has_cable_output
    except Exception:
        return False


def _check_vbcable_installed() -> bool:
    """判断 VB-CABLE 是否已安装：PyAudio 枚举双端点（限时 5s）。"""
    result_box = [False]

    def worker():
        result_box[0] = _check_with_pyaudio()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)
    return result_box[0]


def vbcable_installed() -> bool:
    """VB-CABLE 是否已安装（两个端点齐全才算）。供启动检测判断是否弹框。"""
    return _check_vbcable_installed()


def show_vbcable_dialog(config=None) -> bool:
    """VB-CABLE 检测面板（Windows）。面板结构不随安装状态变化：状态灯、
    双端点说明（48kHz）、驱动卡片（打开控制面板/下载驱动包/安装教程）、
    检测开关。安装成功后面板会自动刷新状态（指示灯变绿）。

    config: 可选 ConfigManager（写入 vbcable_check_enabled）。
    返回 True 表示检测到已安装。
    """
    from pvplatform.system import open_virtual_cable_panel
    from logger import get_logger

    installed = vbcable_installed()

    dlg = QDialog(None)
    dlg.setWindowTitle("PureVox - 虚拟声卡（VB-CABLE）")
    dlg.setMinimumWidth(460)
    dlg.setWindowModality(Qt.ApplicationModal)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)
    layout.setContentsMargins(16, 16, 16, 12)

    # ── 状态行：指示灯 + 标题 ──
    state_row = QHBoxLayout()
    dot = QLabel()
    dot.setFixedSize(12, 12)
    state_label = QLabel()
    state_label.setStyleSheet("font-size: 11pt;")
    state_row.addWidget(dot)
    state_row.addWidget(state_label)
    state_row.addStretch()
    layout.addLayout(state_row)

    # ── 双端点说明（无论是否安装都显示，保持一致结构）──
    tips = QLabel(
        "VB-CABLE 是 VB-Audio 的虚拟声卡，安装后提供一对端点，采样率均设置为 48kHz：\n"
        "\n"
        "• CABLE Input（输入端）—— 接收 PureVox 处理后的音频，经驱动转发到输出端。\n"
        "  请在 PureVox「输出设备」中选择它（本软件的输出写入这里）。\n"
        "\n"
        "• CABLE Output（输出端）—— 作为虚拟麦克风使用，可设置为系统默认麦克风，\n"
        "  供 OBS、直播、聊天、会议等软件选用。\n"
        "\n"
        "数据流向：PureVox → CABLE Input →（驱动转发）→ CABLE Output → 其它软件。"
    )
    tips.setWordWrap(True)
    tips.setStyleSheet("font-size: 10pt;")
    layout.addWidget(tips)

    # ── 驱动卡片：打开控制面板 / 下载驱动包 / 安装视频教程 ──
    card_style = (
        "QFrame { border: 1px solid palette(mid); border-radius: 4px; "
        "background: palette(base); padding: 4px 6px; }"
    )
    card_layout = QVBoxLayout()
    card_layout.setSpacing(4)
    guide_label = QLabel(
        "未检测到 VB-CABLE 驱动：请先下载官方驱动包并安装，装好后本面板会自动刷新。")
    guide_label.setWordWrap(True)
    guide_label.setStyleSheet("font-size: 10pt; color: %s;" % _RED)
    card_layout.addWidget(guide_label)

    row = QHBoxLayout()
    tag = QLabel("  VB-CABLE 驱动 ")
    tag.setStyleSheet("border: 1px solid #888; border-radius: 3px; font-size: 9pt; padding: 1px 4px;")
    row.addWidget(tag)
    row.addStretch()

    btn_panel = QPushButton("打开控制面板")
    btn_panel.setFixedHeight(26)
    btn_panel.setCursor(Qt.PointingHandCursor)
    btn_panel.setToolTip("打开 VB-CABLE 控制面板（需先安装驱动）")
    btn_panel.clicked.connect(lambda _=False: open_virtual_cable_panel(get_logger()))
    row.addWidget(btn_panel)

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

    # ── 检测开关（默认勾选 = 开启检测；开启时才检查，仅未安装会弹框）──
    enabled = config.get("vbcable_check_enabled", True) if config is not None else True
    check_cb = QCheckBox("启动时检测虚拟麦克风（未安装才提醒）")
    check_cb.setChecked(bool(enabled))
    check_cb.setToolTip("默认开启检测：仅在 VB-CABLE 未安装时弹出面板；已安装不会打扰。\n"
                        "如果你有自己的虚拟麦克风或自定义配置，可取消勾选跳过检测。")
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

    # ── 状态刷新：装好驱动后自动变绿，无需重开面板 ──
    def _refresh():
        now = vbcable_installed()
        dot.setStyleSheet("background:%s; border-radius:6px;" % (_GREEN if now else _RED))
        state_label.setText("%s" % ("已安装" if now else "未安装"))
        btn_panel.setEnabled(now)
        guide_label.setVisible(not now)

    timer = QTimer(dlg)
    timer.timeout.connect(_refresh)
    timer.start(2000)
    _refresh()

    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    dlg.show()
    while dlg.isVisible():
        QApplication.processEvents()
        _time.sleep(0.02)
    return installed