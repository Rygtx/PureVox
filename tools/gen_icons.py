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
    assets/icons/audio_icon_on.ico / audio_icon_off.ico
                                   任务栏/托盘多尺寸 ICO（16~64，逐尺寸渲染）
    assets/icons/audio_icon_base.png   512px 应用基图（Linux 打包用）

应用图标设计：32×32 像素画「P」（外部像素编辑器导出的底稿，内嵌为
常量 ART32，零素材依赖）——莫兰迪色系同色配色：描边取灰调中深色并
按运行状态切换（灰绿=运行 / 灰红=停用），字身为描边色向白提浅的同
色系浅调（外深内浅，低饱和耐看）；落点按包围盒居中后把余量 55% 分
给左上（视觉中心微偏右下）。缩放全像素风：整数倍尺寸严格 1:1 最近
邻放大；非整数尺寸按覆盖面积做两级多数投票采样（先决透明/不透明保
剪影完整，再决描边/字身），只输出调色板原色或全透明——无混色、无
半透明、无次像素渲染。ICO 含 16~256 多尺寸帧。

行内小图标设计原则：单色线条、圆角线帽、中性灰；运行时由
ui_pyside6.load_icon() 按 @1x/@2x 装载为 QIcon——不依赖 Qt 标准图标。
用法：python tools/gen_icons.py
"""

import os

from PIL import Image, ImageDraw

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


# ── 应用图标：像素画「P」（内嵌 32×32 像素稿，逐尺寸像素风缩放）──

# 底稿：外部像素编辑器导出（'.'透明 / '0'描边格 / '1'字身格），
# 内嵌常量零素材依赖。
ART32 = [
    "................................",
    "........00000000000.............",
    ".......00111111111000...........",
    ".......011111111111100..........",
    ".......0111111111111100.........",
    ".......0111100000111110.........",
    ".......01110.....0011100........",
    ".......01110......001110........",
    ".......01110.......01110........",
    ".......01110......001110........",
    ".......01110.....0011100........",
    ".......0111100000111110.........",
    ".......0111111111111100.........",
    ".......011111111111100..........",
    ".......01111111111000...........",
    ".......011100000000.............",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    ".......01110....................",
    "........000.....................",
    "................................",
    "................................",
    "................................",
    "................................",
    "................................",
    "................................",
    "................................",
]

EMPTY, EDGE, FILL = 0, 1, 2          # 类索引：透明 / 描边 / 字身

# 莫兰迪色系：描边取灰调中深色，字身 = 同色系提浅（向白混合），
# 外深内浅、低饱和，避免纯白/高饱和大面积刺眼
EDGE_ON = (74, 142, 96)              # 运行：绿描边（明确偏冷）
EDGE_OFF = (206, 104, 92)            # 停用：红描边（明确偏暖）
TINT = 0.15                          # 字身提浅比例（描边色 → 向白混合）
CENTER_BIAS = 55                     # 居中余量再分配：左上占 55%（千分比
                                     # 语义为百分比），视觉中心微偏右下

ART_CLASSES = [[FILL if ch == "1" else EDGE if ch == "0" else EMPTY
                for ch in row] for row in ART32]


def _tint(rgb):
    """描边色向白混合出同色系浅调（字身用）。"""
    return tuple(int(c + (255 - c) * TINT) for c in rgb)


def _place(cls):
    """按内容包围盒整数平移：先几何居中，再把左右/上下余量的 55%
    分给左上（字形落点微偏右下，修正右上视觉重）。不改任何像素。"""
    n = len(cls)
    ys = [y for y in range(n) if any(cls[y])]
    xs = [x for x in range(n) if any(cls[y][x] for y in range(n))]
    y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
    mv, mh = n - (y1 - y0 + 1), n - (x1 - x0 + 1)
    my, mx = -((-mv * CENTER_BIAS) // 100), -((-mh * CENTER_BIAS) // 100)
    out = [[EMPTY] * n for _ in range(n)]
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            out[my + y - y0][mx + x - x0] = cls[y][x]
    return out


ART_CLASSES = _place(ART_CLASSES)


def _pixel_scale(cls, w, size):
    """w×w 类索引网格 → size×size（像素风采样，永不混色）。

    整数倍：严格 1:1 最近邻复制。非整数尺寸：以有理数坐标统计每个
    目标像素覆盖的源格面积权重，两级多数投票——第一级 不透明
    （描边+字身）vs 透明，平票偏向不透明（剪影与单像素线优先）；
    第二级 描边 vs 字身，平票偏向描边（轮廓定义优先）。输出只含
    调色板原色或全透明。
    """
    if size % w == 0:
        k = size // w
        return [[cls[y // k][x // k] for x in range(size)]
                for y in range(size)]
    d = size                             # 有理数坐标的分母
    out = [[EMPTY] * size for _ in range(size)]
    for dy in range(size):
        ya, yb = dy * w, (dy + 1) * w
        iy0, iy1 = ya // d, (yb - 1) // d
        row = out[dy]
        for dx in range(size):
            xa, xb = dx * w, (dx + 1) * w
            ix0, ix1 = xa // d, (xb - 1) // d
            wt_e = wt_f = 0
            for sy in range(iy0, iy1 + 1):
                oy = min(yb, (sy + 1) * d) - max(ya, sy * d)
                if oy <= 0:
                    continue
                crow = cls[sy]
                for sx in range(ix0, ix1 + 1):
                    ox = min(xb, (sx + 1) * d) - max(xa, sx * d)
                    if ox <= 0:
                        continue
                    c = crow[sx]
                    if c == FILL:
                        wt_f += ox * oy
                    elif c == EDGE:
                        wt_e += ox * oy
            area = (xb - xa) * (yb - ya)
            if 2 * (wt_e + wt_f) >= area:            # 平票偏向不透明
                row[dx] = EDGE if wt_e >= wt_f else FILL
    return out


def render_app_icon(size, running=True):
    """渲染单尺寸应用图标：莫兰迪同色系 P（外深内浅）+ 状态色，透明底。"""
    edge = EDGE_ON if running else EDGE_OFF
    cls = _pixel_scale(ART_CLASSES, 32, size)
    pal = {EDGE: edge + (255,), FILL: _tint(edge) + (255,),
           EMPTY: (0, 0, 0, 0)}
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for y in range(size):
        crow = cls[y]
        for x in range(size):
            px[x, y] = pal[crow[x]]
    return img


def gen_app_icons():
    sizes = [16, 20, 24, 32, 48, 64, 128, 256]
    frames_on = {s: render_app_icon(s, True) for s in sizes}
    frames_off = {s: render_app_icon(s, False) for s in sizes}
    # 基底必须取最大帧：ICO 插件会跳过大于基底的尺寸，否则只剩 16px 一帧
    big = max(sizes)
    frames_on[big].save(
        os.path.join(OUT, "audio_icon_on.ico"),
        append_images=[frames_on[s] for s in reversed(sizes) if s != big],
        sizes=[(s, s) for s in sizes])
    frames_off[big].save(
        os.path.join(OUT, "audio_icon_off.ico"),
        append_images=[frames_off[s] for s in reversed(sizes) if s != big],
        sizes=[(s, s) for s in sizes])
    print("[app-icon] audio_icon_on.ico / audio_icon_off.ico")

    base = render_app_icon(BASE_SIZE := 512, True)
    base.save(os.path.join(OUT, "audio_icon_base.png"))
    print(f"[app-icon] audio_icon_base.png ({BASE_SIZE}px)")


def main():
    os.makedirs(OUT, exist_ok=True)
    save(ic_grip(), "grip")
    save(ic_close(), "close")
    save(ic_plus(), "plus")
    save(ic_settings(), "settings")
    gen_app_icons()


if __name__ == "__main__":
    main()
