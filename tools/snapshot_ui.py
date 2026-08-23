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

"""UI 离屏截图工具：构建真实主窗口（用户当前配置），渲染后抓取 PNG。

用法：
    python tools/snapshot_ui.py [输出目录]     # 默认 .py312-src/ui_shots/

用途：外观改造前后对比、布局回归检查。offscreen 渲染，不弹真实窗口。
"""

import os
import sys
import tempfile


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    os.chdir(root)

    out_dir = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(root, ".py312-src", "ui_shots")
    os.makedirs(out_dir, exist_ok=True)

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)
    app.setStyle("windows11")

    import ui_pyside6 as ui
    from config_manager import ConfigManager
    from logger import Logger

    # 用真实用户配置（只读复制到临时文件，避免污染）
    src_cfg = os.path.join(tempfile.gettempdir(), "purevox_snapshot_cfg.json")
    try:
        from user_paths import CONFIG_PATH
        import shutil
        shutil.copyfile(CONFIG_PATH, src_cfg)
        print(f"使用用户配置: {CONFIG_PATH}")
    except Exception as e:
        print(f"无用户配置（{e}），使用默认")

    config = ConfigManager(src_cfg)
    config.load_config()
    logger = Logger()

    window = ui.MainWindow(config, logger)
    app_main = ui.MainApp()
    app_main._setup(window, root, config)
    app_main._apply_style()
    ui._apply_theme(app)
    app_main._create_ui(window, config, None, logger)

    window.setFixedSize(420, 700)
    window.show()
    for _ in range(15):
        app.processEvents()
        import time
        time.sleep(0.05)

    pix = window.grab()
    out = os.path.join(out_dir, "main_420x700.png")
    pix.save(out)
    print(f"[saved] {out}")

    # 宽窗：验证横向滚动问题是否复现
    window.setFixedSize(560, 700)
    for _ in range(10):
        app.processEvents()
        import time
        time.sleep(0.05)
    pix = window.grab()
    out = os.path.join(out_dir, "main_560x700.png")
    pix.save(out)
    print(f"[saved] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
