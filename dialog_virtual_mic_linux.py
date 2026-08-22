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

# dialog_virtual_mic_linux.py
# Linux 虚拟声卡面板：PureVox 不自动创建虚拟麦克风，由用户在此面板
# 手动「创建」/「清理」（purevox_out null-sink + monitor + remap-source）。
import time as _time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from pvplatform.system import virtual_mic_ready, ensure_virtual_mic, remove_virtual_mic

_GREEN = "#3aa76d"
_RED = "#d9534f"


def _status_dot(ready: bool) -> QLabel:
    """状态指示灯：绿=已就绪，红=未安装/未创建。"""
    dot = QLabel()
    dot.setFixedSize(12, 12)
    dot.setStyleSheet("background:%s; border-radius:6px;" % (_GREEN if ready else _RED))
    return dot


def _refresh_state(state_label, status_dot):
    ready = virtual_mic_ready()
    state_label.setText("状态：%s" % ("已就绪" if ready else "未就绪（未创建或已被清理）"))
    status_dot.setStyleSheet("background:%s; border-radius:6px;" % (_GREEN if ready else _RED))


def show_virtual_mic_dialog(logger, refresh_devices=None, api_type=0):
    """Linux 虚拟声卡面板。refresh_devices: 可选回调（创建/清理后刷新设备下拉）。
    api_type: 兼容旧签名保留（现仅 PipeWire 单一本地接口，不再影响文案）。"""
    dlg = QDialog(None)
    dlg.setWindowTitle("PureVox - 虚拟声卡")
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)
    layout.setContentsMargins(16, 16, 16, 12)

    title = QLabel("虚拟声卡（Linux）")
    title.setStyleSheet("font-size: 11pt;")
    layout.addWidget(title)

    state_row = QHBoxLayout()
    status_dot = _status_dot(False)
    state_row.addWidget(status_dot)
    state_label = QLabel()
    state_label.setStyleSheet("font-size: 10pt;")
    state_row.addWidget(state_label)
    state_row.addStretch()
    layout.addLayout(state_row)
    _refresh_state(state_label, status_dot)

    # 两个出口端点说明
    tips = QLabel(
        "PureVox 虚拟麦克风由两个出口组成（创建后其它软件按需选用）：\n"
        "• PureVox 虚拟麦克风（purevox_out.monitor）—— 宽口径源，供绝大多数软件选用；\n"
        "• PureVox mic（purevox_mic）—— 供 OBS 等只列\"真源\"的软件使用（由前者的\n"
        "  重映射而来）。\n"
        "降噪输出自动写入本虚拟麦克风，无需额外选择。\n"
        "PureVox 不会自动创建虚拟声卡：需要时点\"创建\"，不用时点\"清理\"。"
    )
    tips.setWordWrap(True)
    tips.setStyleSheet("font-size: 10pt;")
    layout.addWidget(tips)

    def _refresh_buttons():
        ready = virtual_mic_ready()
        btn_action.setText("清理" if ready else "创建")
        btn_action.setEnabled(True)

    def _on_action():
        btn_action.setEnabled(False)
        QApplication.processEvents()
        if virtual_mic_ready():
            remove_virtual_mic(logger)
        else:
            ensure_virtual_mic(logger)
        if refresh_devices:
            QTimer.singleShot(300, refresh_devices)
        _refresh_state(state_label, status_dot)
        _refresh_buttons()

    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_action = QPushButton()
    btn_action.setFixedHeight(28)
    btn_action.setFixedWidth(88)
    btn_action.setCursor(Qt.PointingHandCursor)
    btn_action.clicked.connect(_on_action)
    btn_row.addWidget(btn_action)
    btn_close = QPushButton("关闭")
    btn_close.setFixedHeight(28)
    btn_close.setFixedWidth(88)
    btn_close.setCursor(Qt.PointingHandCursor)
    btn_close.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_close)
    layout.addLayout(btn_row)

    _refresh_buttons()

    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    dlg.show()
    while dlg.isVisible():
        QApplication.processEvents()
        _time.sleep(0.02)