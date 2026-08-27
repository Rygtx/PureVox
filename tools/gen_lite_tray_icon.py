# PureVox Lite — 托盘/应用图标资产生成（一次性工具，产物入库为唯一事实）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 64px 母版（像素字体 P，莫兰迪双色描边）最近邻出全尺寸帧；
# 运行时（dev / 冻结 / 打包）一律直接读 assets/icons/lite_tray.ico 与
# lite_tray.png，本脚本仅在需要改设计时手动重跑。

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "assets", "fonts", "ark-pixel-12px-monospaced-zh_cn.ttf")
OUT_DIR = os.path.join(ROOT, "assets", "icons")

master = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
d = ImageDraw.Draw(master)
pf = ImageFont.truetype(FONT, 56)
bbox = d.textbbox((0, 0), "P", font=pf, stroke_width=3)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
d.text(((64 - tw) // 2, (64 - th) // 2 - 2), "P", fill="#6D4C41",
       font=pf, stroke_width=3, stroke_fill="#FFB74D")

master.save(os.path.join(OUT_DIR, "lite_tray.png"))

# ico 帧梯：全部由 64px 母版最近邻采样（128/256 为整数倍，像素严格对齐）
master.save(
    os.path.join(OUT_DIR, "lite_tray.ico"),
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48),
           (32, 32), (24, 24), (20, 20), (16, 16)])

print("written:")
for f in ("lite_tray.png", "lite_tray.ico"):
    p = os.path.join(OUT_DIR, f)
    print(" ", p, os.path.getsize(p), "bytes")
