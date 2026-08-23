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
PySide6 版本启动脚本
"""

import sys
import os
import datetime
from pvplatform.system import acquire_single_instance

# PySide6 高 DPI 优化 - 必须在 QApplication 创建前设置
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"  # 启用高 DPI 缩放
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"  # 精确缩放
os.environ["QT_USE_PHYSICAL_DPI"] = "0"  # 使用逻辑 DPI


def _early_log(msg: str, tag: str = "SYS") -> None:
    """早期启动阶段直接写日志文件（logger 模块尚未加载时使用）。"""
    try:
        from user_paths import get_log_path
        now = datetime.datetime.now()
        ts = now.strftime('%Y-%m-%d %H:%M:%S') + f'.{now.microsecond // 1000:03d}'
        with open(get_log_path(), 'a', encoding='utf-8') as f:
            f.write(f'[{ts}] [{tag:>4s}] {msg}\n')
    except Exception:
        pass


# ── 最早可记录的启动时间点 ──
_early_log("PureVox 启动中...")

# 检查单实例（Windows 命名 Mutex / Linux flock，见 platform.system）
if not acquire_single_instance("PureVox"):
    _early_log("检测到重复启动，本次启动已终止")
    # 用 Qt 消息框（可显示应用图标），打包为 --noconsole 时 print 不可见
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtGui import QIcon
        _app = QApplication(sys.argv)
        _res = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        _ico = os.path.join(_res, "assets", "icons", "audio_icon_off.ico")
        _box = QMessageBox()
        _box.setWindowTitle("PureVox")
        _box.setText("PureVox 已在运行，请勿重复启动。")
        _box.setIcon(QMessageBox.Information)
        if os.path.exists(_ico):
            _box.setWindowIcon(QIcon(_ico))
        _box.exec()
    except Exception:
        print('程序已在运行，禁止重复启动')
    sys.exit(1)

# 导入版本信息
try:
    from _build_version import BUILD_DATE
    print(f"PureVox {BUILD_DATE}")
except ImportError:
    print("PureVox 开发版")

# 导入并运行 PySide6 版本
from ui_pyside6 import run_app

if __name__ == "__main__":
    run_app()
