#!/usr/bin/env python
# -*- coding: utf-8 -*-
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
"""图标生成脚本（PIL 程序化绘制，零外部素材依赖）。

产出：
    assets/icons/<name>.png        16px（逻辑像素 @1x）
    assets/icons/<name>@2x.png     32px（高 DPI @2x）
    assets/icons/audio_icon.ico        任务栏/托盘/EXE 多尺寸 ICO（16~256）
    assets/icons/audio_icon_base.png   512px 应用基图（Linux 打包用）

应用图标设计：与 Lite 托盘图标（tools/gen_lite_tray_icon.py）同源——
ark-pixel 12px 像素字体「P」（224px 字号 + 24px 描边，256px 母版），单一
绿色配色（深绿字身 + 亮绿描边），不再区分运行/停用双态。母版渲染后裁剪到
内容边界（±8% 内边距），最大化托盘填充率。512 基图由裁剪后母版整数倍
最近邻放大；ICO 以 256px 最近邻帧为基底，其余帧由插件降采样。

行内小图标设计原则：单色线条、圆角线帽、中性灰；运行时由
ui_pyside6.load_icon() 按 @1x/@2x 装载为 QIcon——不依赖 Qt 标准图标。
用法：python tools/gen_icons.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icons")
SS = 4                    # 超采样倍率
GRID = 24                 # 图标网格（逻辑）
FG = (154, 160, 166, 255)      # 中性前景灰
FG_DIM = (110, 115, 122, 255)  # 更弱一级（拖柄）

LINE_W = int(2 * SS)


def canvas():
    size = GRID * SS
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def dot(d, cx, cy, r, fill=FG):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def round_line(d, p1, p2, fill=FG, w=LINE_W):
    """带圆帽的粗线：线段 + 两端小圆。"""
    d.line([p1, p2], fill=fill, width=w)
    r = w / 2 - SS * 0.25
    if r > 0:
        dot(d, p1[0], p1[1], r, fill)
        dot(d, p2[0], p2[1], r, fill)


def save(img, name):
    base = os.path.join(OUT, name + ".png")
    img.resize((GRID, GRID), Image.LANCZOS).save(base)
    img.resize((GRID * 2, GRID * 2), Image.LANCZOS).save(
        os.path.join(OUT, name + "@2x.png"))
    print(f"[icon] {name}.png")


# ── 各图标定义（24px 网格坐标）──

def ic_grip():
    """拖拽手柄：6 圆点。"""
    img, d = canvas()
    xs = [9.5, 14.5]
    ys = [7, 12, 17]
    for y in ys:
        for x in xs:
            dot(d, x * SS, y * SS, 1.6 * SS, FG_DIM)
    return img


def ic_close():
    """关闭 ×。"""
    img, d = canvas()
    a, b, c, e = 7, 7, 17, 17
    round_line(d, (a * SS, b * SS), (c * SS, e * SS))
    round_line(d, (c * SS, b * SS), (a * SS, e * SS))
    return img


def ic_plus():
    """添加 +。"""
    img, d = canvas()
    m = 12
    round_line(d, (6 * SS, m * SS), (18 * SS, m * SS))
    round_line(d, (m * SS, 6 * SS), (m * SS, 18 * SS))
    return img


def ic_settings():
    """设置：三条调节滑杆（水平线 + 错位旋钮）。"""
    img, d = canvas()
    rows = [(7, 10), (12, 15), (17, 8)]
    for y, kx in rows:
        round_line(d, (5 * SS, y * SS), (19 * SS, y * SS), FG, LINE_W - SS // 2)
        dot(d, kx * SS, y * SS, 2.4 * SS, FG)
    return img


# ── 应用图标：像素字体「P」（与 Lite 托盘同源设计，单一绿色，无双态）──

FONT_PATH = os.path.join(ROOT, "assets", "fonts",
                         "ark-pixel-12px-monospaced-zh_cn.ttf")
MASTER = 512                    # 母版尺寸（512 基图 + 各帧独立渲染）
FONT_SIZE = 504
P_SCALE = 1.4                   # 深色 P 放大倍率
FILL = (100, 180, 255, 255)      # 字身：亮蓝（反转测试）
STROKE = (15, 35, 80, 255)      # 描边：深蓝（反转测试）


def _render_frame(size):
    """亮色 P 扩散做背景，深色 P 填满画布。"""
    fs = max(8, int(size * FONT_SIZE / MASTER))
    pf = ImageFont.truetype(FONT_PATH, fs)
    # 1. 画 P，测实际像素范围，缩放填满 85% 画布
    tmp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    td.text((0, 0), "P", fill=(255, 255, 255, 255), font=pf)
    cb = tmp.getbbox()
    if not cb:
        return tmp
    pw, ph = cb[2] - cb[0], cb[3] - cb[1]
    need = size * 0.85
    if pw < need or ph < need:
        s = need / max(pw, ph)
        fs = max(8, int(fs * s))
        pf = ImageFont.truetype(FONT_PATH, fs)
        tmp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        td.text((0, 0), "P", fill=(255, 255, 255, 255), font=pf)
        cb = tmp.getbbox()
        pw, ph = cb[2] - cb[0], cb[3] - cb[1]
    # 2. 以实际像素居中
    ox = (size - pw) // 2 - cb[0]
    oy = (size - ph) // 2 - cb[1]
    # 3. 扩散量按比例
    spread = max(1, int(size * 0.06))
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(result)
    for dx in range(-spread, spread + 1):
        for dy in range(-spread, spread + 1):
            rd.text((ox + dx, oy + dy), "P", fill=STROKE, font=pf)
    # 4. 深色 P 原位
    rd.text((ox, oy), "P", fill=FILL, font=pf)
    return result


def gen_app_icons():
    # 512 基图（Linux 打包）
    _render_frame(MASTER).save(os.path.join(OUT, "audio_icon_base.png"))
    # ICO：各尺寸独立渲染，1:1 像素无缩放
    sizes = [256, 128, 96, 72, 64, 60, 48, 40, 36, 32, 30, 24, 20, 16]
    frames = [_render_frame(s) for s in sizes]
    frames[0].save(
        os.path.join(OUT, "audio_icon.ico"),
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:])
    print("[app-icon] audio_icon.ico / audio_icon_base.png (512px)")


def main():
    os.makedirs(OUT, exist_ok=True)
    save(ic_grip(), "grip")
    save(ic_close(), "close")
    save(ic_plus(), "plus")
    save(ic_settings(), "settings")
    gen_app_icons()


if __name__ == "__main__":
    main()
