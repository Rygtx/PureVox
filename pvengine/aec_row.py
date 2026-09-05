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

"""行级回声消除——一个 `echo_cancel` 输入行对应一个 AecRow。

信号流（该行 mic 专属，不经过 fx 链）：
  mic_hop → AEC（far 直达）→ 混音

far 端经 FarTap 按 mic hop 主时钟拉齐后**直达**本行，不经过任何
fx 处理。AecEngine 会话多行共享（`get_shared_engine` 按模型路径缓存），
cache / FarTap 为行私有状态。

选型说明（先扩展再新建）：旧 AecStage 是单例 fx 语义（共享 cache，
多行无法独立），且挂在 fx 链上由 FrameContext 喂 far；行级 AEC 需要
"一行一 cache + far 直达 + mic 增益"，旧结构无法扩展，故单列此文件；
旧 AecStage 已删除，行级 AEC 的唯一实现点在此。
"""

import numpy as np

from pvengine.components.aec import AecEngine
from pvengine.context import HOP_LENGTH, SAMPLE_RATE
from pvengine.dsp.far_sync import FarTap

_engines: dict = {}


def get_shared_engine(model_path: str) -> AecEngine:
    """按模型路径缓存的多行共享 AEC 会话（进程内单例）。"""
    eng = _engines.get(model_path)
    if eng is None or getattr(eng, "sess", None) is None:
        eng = AecEngine(model_path)
        _engines[model_path] = eng
    return eng


def find_model_file(name: str) -> str:
    """按模型相对路径定位（PyInstaller 资源目录 → 仓库根 → CWD）。"""
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    bases = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    bases.append(os.path.dirname(here))
    bases.append(here)
    for base in bases:
        cand = os.path.join(base, name)
        if os.path.isfile(cand):
            return cand
    return name


class AecRow:
    """一行 AEC 的全部行级状态：FarTap + AEC cache + far 延迟缓冲。"""

    MAX_DELAY_SAMPLES = 24000  # 500ms @ 48kHz

    def __init__(self, model_path: str, far_sample_rate: int = SAMPLE_RATE,
                 far_gain_db: float = -20.0):
        self.engine = get_shared_engine(model_path)
        self.cache = self.engine.new_state()
        self.far_sample_rate = int(far_sample_rate or SAMPLE_RATE)
        # far 对齐 + 48k 重采样收敛在 FarTap（与 loopback 输入行共用）
        self.tap = FarTap(self.far_sample_rate, HOP_LENGTH)
        # 参考音量：只缩放进模型的 far 帧（调试用，默认 0dB 直达）
        self._far_gain = 10.0 ** (float(far_gain_db) / 20.0)
        self.last_far = np.zeros(HOP_LENGTH, dtype=np.float32)
        # far 手动延迟缓冲（样本级，0–500ms）
        self._delay_samples = 0
        self._delay_buf = np.zeros(self.MAX_DELAY_SAMPLES, dtype=np.float32)
        self._delay_write_pos = 0

    def set_far_gain_db(self, db: float) -> None:
        self._far_gain = 10.0 ** (float(db) / 20.0)

    def set_delay_ms(self, ms: float) -> None:
        """设置 far 信号延迟（毫秒），立即生效。"""
        self._delay_samples = max(0, min(self.MAX_DELAY_SAMPLES,
                                         int(round(ms * SAMPLE_RATE / 1000.0))))

    def get_delay_ms(self) -> float:
        return self._delay_samples * 1000.0 / SAMPLE_RATE

    def push_far(self, samples) -> None:
        """far 设备域新到样本推入对齐器（引擎线程每 hop 先搬后取）。"""
        self.tap.push(samples)

    def _delay_apply(self, far_ref: np.ndarray) -> np.ndarray:
        """对 far_ref 施加样本级延迟（环形缓冲，零填充启动阶段）。"""
        n = len(far_ref)
        d = self._delay_samples
        if d == 0:
            return far_ref
        out = np.empty(n, dtype=np.float32)
        buf = self._delay_buf
        wp = self._delay_write_pos
        for i in range(n):
            buf[wp] = far_ref[i]
            rp = (wp - d) % self.MAX_DELAY_SAMPLES
            out[i] = buf[rp]
            wp = (wp + 1) % self.MAX_DELAY_SAMPLES
        self._delay_write_pos = wp
        return out

    def process_mic(self, mic_hop) -> np.ndarray:
        """本路 mic 一 hop → AEC → 输出一 hop（恒满帧）。

        far 经 FarTap 拉齐 + 手动延迟后直达模型，不经过任何 fx。
        """
        mic = np.asarray(mic_hop, dtype=np.float32).reshape(-1)
        far_ref = self.tap.pull()
        far_ref = self._delay_apply(far_ref)
        if self._far_gain != 1.0:
            far_ref = far_ref * np.float32(self._far_gain)
        self.last_far = far_ref
        out, self.cache = self.engine.process_frame(mic, far_ref, self.cache)
        return np.asarray(out, dtype=np.float32).reshape(-1)

    def diag(self) -> dict:
        d = self.tap.diag()
        d["cache_norm"] = float((self.cache * self.cache).sum() ** 0.5)
        return d
